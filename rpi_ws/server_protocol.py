from twisted.python import log
from twisted.internet import threads, reactor
import twisted.internet.protocol as twistedsockets
from autobahn.websocket import WebSocketServerFactory, WebSocketServerProtocol, HttpException
import autobahn.httpstatus as httpstatus
from twisted.web import resource
import json
import time
import os
import binascii
import hashlib
import settings, common_protocol, buffer
from hashlib import sha1
import hmac
import urllib2, urllib
import math

import twisted.internet.interfaces

class SiteComm(resource.Resource):
    """
    To handle requests from the website
    """
    isLeaf = True

    def __init__(self, ws_factory, *args, **kwargs):
        resource.Resource.__init__(self, *args, **kwargs)
        self.ws_factory = ws_factory

    def render_GET(self, request):
        request.setHeader("Content-Type", "application/json")
        return "%s" % (str(self.ws_factory.rpi_clients),)

    def render_POST(self, request):
        # should be called to update configs by admin change

        request.setHeader("Content-Type", "application/json")

        try:
            rpis = json.loads(request.args['json'][0])

            for rpi in rpis:
                if self.ws_factory.debug:
                    log.msg('render_POST - Received config for RPI %s' % rpi['mac'])
        except:
            if self.ws_factory.debug:
                log.msg('render_POST -  Error parsing rpi configs')
            return 'error'

        # delegate request to the WS factory
        self.ws_factory.config_rpi(rpi)

        return 'ok'


    def register_rpi(self, rpi):
        # we need mac, ip, interface desc
        payload = {}
        payload['mac'] = rpi.mac
        payload['ip'] = rpi.protocol.peer.host
        payload['iface'] = rpi.iface

        post_data = {'json':json.dumps(payload)}
        post_data = urllib.urlencode(post_data)
        try:
            url = urllib2.Request('http://%s/ws_comm/register/' % settings.SITE_SERVER_ADDRESS, post_data)
            url_response = urllib2.urlopen(url)
            url_response.read()
        except:
            pass
        # TODO: success validation

        # notify users
        reactor.callFromThread(self.ws_factory.register_rpi_wsite, rpi)
        # register should return configs


    def disconnect_rpi(self, rpi):
        payload = {}
        payload['mac'] = rpi.mac

        post_data = {'json':json.dumps(payload)}
        post_data = urllib.urlencode(post_data)
        try:
            url = urllib2.Request('http://%s/ws_comm/disconnect/' % settings.SITE_SERVER_ADDRESS, post_data)
            url_response = urllib2.urlopen(url)
            url_response.read()
        except:
            pass

        # notify users
        reactor.callFromThread(self.ws_factory.disconnect_rpi_wsite, rpi)


class ServerState(common_protocol.State):
    def __init__(self, client):
        self.client = client

    def activated(self):
        if self.client.protocol.debug:
            log.msg("%s.activated()" % self.__class__.__name__)

    def deactivated(self):
        if self.client.protocol.debug:
            log.msg("%s.deactivated()" % self.__class__.__name__)

class Client(common_protocol.ProtocolState):
    def __init__(self, protocol):
        common_protocol.ProtocolState.__init__(self)
        self.protocol = protocol

    def onMessage(self, msg):
        try:
            state = self.state_stack.pop_wr()
        except IndexError:
            if self.protocol.factory.debug:
                log.msg("%s.onMessage - Received a message in an unknown state, ignored", self.__class__.__name__)
        state.onMessage(msg)

    def onClose(self, wasClean, code, reason):
        pass

    def onOpen(self):
        pass

"""
User client related protocol and states
"""

class UserState(ServerState):
    """
    Only state for users
    Users can do the following:
        query online rpis
        register as a user of a rpi
            read data stream for rpi interfaces
            request write to interface
                Note: this will be sent back to the user if successful in the data stream

    Server is responsible for:
        notifying user when rpi disconnects

    """
    def __init__(self, client):
        super(UserState, self).__init__(client)

    def activated(self):
        super(UserState, self).activated()

    def onMessage(self, msg):
        try:
            msg = json.loads(msg)
        except:
            if self.client.protocol.debug:
                log.msg('UserState.onMessage - JSON error, dropping')
            self.client.protocol.failConnection()

        if msg['cmd'] == common_protocol.UserClientCommands.CONNECT_RPI:
            mac = msg['rpi_mac']
            self.client.register_to_rpi(mac)



class UserClient(Client):
    def __init__(self, protocol):
        Client.__init__(self, protocol)
        self.associated_rpi = None
        self.streaming_buffer = None

    def register_to_rpi(self, rpi_mac):
        rpi = self.protocol.factory.get_rpi(rpi_mac)
        if rpi:
            self.streaming_buffer = buffer.UpdateDict()
            self.associated_rpi = rpi
            self.protocol.factory.register_user_to_rpi(self, self.associated_rpi)

    def unregister_to_rpi(self):
        if self.associated_rpi is not None:
            self.protocol.factory.unregister_user_to_rpi(self, self.associated_rpi)
            self.associated_rpi = None

    def notifyRPIState(self, rpi, state):
        if state == 'config':
            if self.associated_rpi is not rpi:
                return
        msg = {'cmd':common_protocol.ServerCommands.RPI_STATE_CHANGE, 'rpi_mac':rpi.mac, 'rpi_state':state}
        self.protocol.sendMessage(json.dumps(msg))

    def onClose(self, wasClean, code, reason):
        self.protocol.factory.disconnect_user(self)

    def onOpen(self):
        self.push_state(UserState(self))
        self.protocol.factory.register_user(self)

"""
RPI client related protocol and states
"""

class RPIStreamState(ServerState):
    """
    In this state the RPI has been configured and is streaming data
    """
    def __init__(self, client, reads, writes):
        super(RPIStreamState, self).__init__(client)
        # {'cls:ADC, port:3': {'cls_name':'ADC', 'ch_port':3, 'equations': ['zzzz', 'asdfadfad']}}
        self.config_reads = reads
        self.config_writes = writes
        self.read_data_buffer = {}
        self.read_data_buffer_eq = {}
        self.write_data_buffer = {}
        self.datamsgcount_ack = 0

    def evaluate_eq(self, eq, value):
        if eq != '':
            # TODO: fix security
            x = value
            new_value = eval(eq)
        else:
            new_value = value
        return new_value

    def deactivated(self):
        super(RPIStreamState, self).deactivated()

    def onMessage(self, msg):
        msg = json.loads(msg)

        if msg['cmd'] == common_protocol.RPIClientCommands.DROP_TO_CONFIG_OK:
            # order here is important, pop first!
            self.client.pop_state()
            self.client.current_state().config_io(self.delegate_config_reads, self.delegate_config_writes)

        if msg['cmd'] == common_protocol.RPIClientCommands.DATA:
            self.datamsgcount_ack += 1
            read_data = msg['read']
            write_data = msg['write']
            for key, value in read_data.iteritems():
                self.read_data_buffer[key] = value
                # perform equation operations here on values
                # key: 'cls:%s, port:%d, eq:%s'
                if key in self.config_reads:
                    for eq in self.config_reads[key]['equations']:
                        new_key = 'cls:%s, port:%d, eq:%s' % (
                            self.config_reads[key]['cls_name'],
                            self.config_reads[key]['ch_port'],
                            eq,
                        )
                        self.read_data_buffer_eq[new_key] = self.evaluate_eq(eq, value)
                else:
                    # TODO: drop to config state or something, remote config seems to be invalid
                    pass
            if self.client.protocol.debug:
                log.msg('RPIStreamState - EQs: %s' % str(self.read_data_buffer_eq))
            for key, value in write_data.iteritems():
                # equations don't apply to read write_data values here
                # write equations are applied at write time
                self.write_data_buffer[key] = value
            # notify factory to update listening clients
            if self.datamsgcount_ack >= 5:
                msg = {'cmd':common_protocol.ServerCommands.ACK_DATA, 'ack_count':self.datamsgcount_ack}
                self.client.protocol.sendMessage(json.dumps(msg))
                self.datamsgcount_ack = 0

    def resume_streaming(self):
        msg = {'cmd':common_protocol.ServerCommands.RESUME_STREAMING}
        self.client.protocol.sendMessage(json.dumps(msg))

    def pause_streaming(self):
        msg = {'cmd':common_protocol.ServerCommands.PAUSE_STREAMING}
        self.client.protocol.sendMessage(json.dumps(msg))

    def drop_to_config(self, reads, writes):
        # drop remote RPI to config state
        msg = {'cmd':common_protocol.ServerCommands.DROP_TO_CONFIG}
        self.client.protocol.sendMessage(json.dumps(msg))
        self.delegate_config_reads = reads
        self.delegate_config_writes = writes


class RPIConfigState(ServerState):
    """
    In this state, the RPI is waiting to be configured.
    Server is not required to configure the RPI immediately.
    """
    def __init__(self, client):
        super(RPIConfigState, self).__init__(client)

    def onMessage(self, msg):
        msg = json.loads(msg)

        if msg['cmd'] == common_protocol.RPIClientCommands.CONFIG_OK:
            self.client.push_state(RPIStreamState(self.client,
                reads=self.config_reads,
                writes=self.config_writes
            ))
        elif msg['cmd'] == common_protocol.RPIClientCommands.CONFIG_FAIL:
            if self.client.protocol.debug:
                log.msg('RPIConfigState - RPI failed to configure')
            # TODO: Notify web server

    def config_io(self, reads, writes):
        """
        read/writes are lsts of dicts with the following:
            'ch_port':  integer or boolean (check cls req)
            'equation': empty, or python style math
            'cls_name': class name as string, ex) 'ADC'

        Returns True/False for success
        """
        self.display_reads = reads
        self.display_writes = writes

        # convert format from list of displays:
        # [{u'ch_port': 3, u'equation': u'', u'cls_name': u'ADC'}, {u'ch_port': 3, u'equation': u'', u'cls_name': u'ADC'}]
        # [{u'ch_port': 3, u'equation': u'', u'cls_name': u'GPIOOutput'}]
        # to data required:
        # {'cls:ADC, port:3': {'cls_name':'ADC', 'ch_port':3, 'equations': ['zzzz', 'asdfadfad']}}
        # this removes duplicates via associated key

        def format_io(io_collection):
            # deal with duplicates...........
            # duplicate equations allowed, duplicate instances not allowed
            instanced_io_dict = {}
            for io in io_collection:
                cls_str = io['cls_name']
                ch_port = io['ch_port']
                equation = io['equation']

                key = 'cls:%s, port:%s' % (cls_str, ch_port)
                if key not in instanced_io_dict:
                    io_new_dict = {'cls_name':cls_str, 'ch_port':ch_port}
                    io_new_dict['equations'] = [equation]
                    instanced_io_dict[key] = io_new_dict
                else:
                    # we can have more then one equation per instance
                    existing_instance = instanced_io_dict[key]
                    equations = existing_instance['equations']
                    if equation not in equations:
                        equations.append(equation)

            return instanced_io_dict

        self.config_reads = format_io(reads)
        self.config_writes = format_io(writes)

        log.msg(self.config_reads)
        log.msg(self.config_writes)

        if self.client.protocol.debug:
            log.msg('RPIConfigState - Pushing configs to remote RPI')

        msg = {'cmd':common_protocol.ServerCommands.CONFIG,
               'payload':{'read':self.config_reads, 'write':self.config_writes}}

        self.client.protocol.sendMessage(json.dumps(msg))


class RPIRegisterState(ServerState):
    def __init__(self, client):
        super(RPIRegisterState, self).__init__(client)
        self.registered = False
        self.re_message_count = 0

    def onMessage(self, msg):
        if self.re_message_count == 0 and not self.registered:
            # msg contains a register request
            parsed = json.loads(msg)
            self.client.mac = parsed['mac']
            self.client.iface = parsed['iface']
            if self.client.protocol.debug:
                log.msg("RPIClient.onMessage - Register Request from %s" % self.client.mac)

            # confirm legitimacy of request
            self.hmac_authorize()
            self.re_message_count += 1
            return

        if self.re_message_count == 1 and not self.registered:
            # msg contains HMAC response
            parsed = json.loads(msg)
            if parsed['cmd'] != common_protocol.ServerCommands.AUTH:
                if self.client.protocol.debug:
                    log.msg("RPIClient.onMessage - Auth fail, dropping")
                self.client.protocol.failConnection()

            # verify expected response
            if self.hamc_token == parsed['payload']['token']:
                self.registered = True
                self.re_message_count = 0
                if self.client.protocol.debug:
                    log.msg("RPIClient.onMessage - Successful registration")
                self.client.protocol.sendMessage(json.dumps({'cmd':common_protocol.ServerCommands.ACK}))
                self.client.push_state(RPIConfigState(self.client))
                # add to dictionary of clients in the factory
                self.client.protocol.factory.register_rpi(self.client)
            else:
                if self.client.protocol.debug:
                    self.client.protocol.failConnection()
                    log.msg("RPIClient.onMessage - Registration failed")
            return

    def hmac_authorize(self):
        _time = time.time()
        _rand = binascii.hexlify(os.urandom(32))
        hashed = hashlib.sha1(str(_time) + _rand).digest()
        self.rand_token = binascii.b2a_base64(hashed)[:-1]

        # calculate expected response
        hashed = hmac.new(settings.HMAC_TOKEN, self.client.mac + self.rand_token, sha1)
        self.hamc_token = binascii.b2a_base64(hashed.digest())[:-1]

        # send token
        msg = {'cmd':common_protocol.ServerCommands.AUTH, 'payload':{'token':self.rand_token}}
        self.client.protocol.sendMessage(json.dumps(msg))



class RPIClient(Client):
    def __init__(self, protocol):
        Client.__init__(self, protocol)

    def onClose(self, wasClean, code, reason):
        # if we're registered remove ourselves from active client list
        if hasattr(self, 'mac'):
            self.protocol.factory.disconnect_rpi(self)

    def onOpen(self):
        self.push_state(RPIRegisterState(self))

    def pause_streaming(self):
        try:
            state = self.current_state()
        except IndexError:
            # RPI has no states
            return False

        if isinstance(state, RPIStreamState):
            state.pause_streaming()
            return True
        return False

    def resume_streaming(self):
        try:
            state = self.current_state()
        except IndexError:
            # RPI has no states
            return False

        if isinstance(state, RPIStreamState):
            state.resume_streaming()
            return True
        return False

    def config_io(self, reads, writes):
        """
        read/writes are lsts of dicts with the following:
            'ch_port':  integer or boolean (check cls req)
            'equation': empty, or python style math
            'cls_name': class name as string, ex) 'ADC'

        Returns True/False for success
        """
        # check the state of the RPI client
        try:
            state = self.current_state()
        except IndexError:
            # RPI has no states
            return False

        if isinstance(state, RPIConfigState):
            # ready to be configured
            # RPI was waiting for config
            pass
        elif isinstance(state, RPIStreamState):
            # RPI is being re-configured
            state.drop_to_config(reads, writes)
            # config has to be delegated
            return True
        else:
            # RPI can't be put into a config state, fail
            return False

        state = self.current_state()
        # delegate the job to the config state
        return state.config_io(reads, writes)


class RPIServerProtocol(WebSocketServerProtocol):
    """
    Base server protocol, instantiates child protocols
    """

    def __init__(self):
        self.client = None

    def onConnect(self, connectionRequest):
        def user(headers):
            if self.debug:
                log.msg("RPIServerProtocol.onConnect - User connected")
            return UserClient(self)
        def rpi(headers):
            # check user agent
            if 'user-agent' in headers:
                if headers['user-agent'] == settings.RPI_USER_AGENT:
                    if self.debug:
                        log.msg("RPIServerProtocol.onConnect - RPI connected")
                    return RPIClient(self)
            raise HttpException(httpstatus.HTTP_STATUS_CODE_FORBIDDEN[0], httpstatus.HTTP_STATUS_CODE_FORBIDDEN[1])

        paths = {
            '/':user,
            '/rpi/':rpi,
            '/rpi':rpi,
        }

        if connectionRequest.path not in paths:
            raise HttpException(httpstatus.HTTP_STATUS_CODE_NOT_FOUND[0], httpstatus.HTTP_STATUS_CODE_NOT_FOUND[1])

        self.client = paths[connectionRequest.path](connectionRequest.headers)

    def onMessage(self, msg, binary):
        """
        Message received from client
        """
        if self.client is None:
            if self.debug:
                log.msg("RPIServerProtocol.onMessage - No Client type")
            self.failConnection()

        self.client.onMessage(msg)

    def onOpen(self):
        WebSocketServerProtocol.onOpen(self)
        if self.client is not None:
            self.client.onOpen()

    def onClose(self, wasClean, code, reason):
        """
        Connect closed, cleanup
        """
        # base logs
        WebSocketServerProtocol.onClose(self, wasClean, code, reason)
        if self.client is None:
            if self.debug:
                log.msg("RPIServerProtocol.onClose - No Client type")
            return

        self.client.onClose(wasClean, code, reason)


class RPISocketServerFactory(WebSocketServerFactory):
    """
    Manages every RPI connected to the server.
    """
    def __init__(self, *args, **kwargs):
        WebSocketServerFactory.__init__(self, *args, **kwargs)

        # safari
        self.allowHixie76 = True

        # identify rpi's by their macs
        # identify user by peerstr
        self.rpi_clients = {}
        self.user_client = {}
        # key RPI mac, value list of user clients
        self.rpi_clients_registered_users = {}

    def register_user_to_rpi(self, client, rpi):
        if len(self.rpi_clients_registered_users[rpi.mac]) == 0:
            # RPI wasn't streaming, start streaming!
            rpi.resume_streaming()
        if client not in self.rpi_clients_registered_users[rpi.mac]:
            self.rpi_clients_registered_users[rpi.mac].append(client)
            if self.debug:
                log.msg('RPISocketServerFactory.register_user_to_rpi rpi:%s user:%s' %
                        (rpi.mac, client.protocol.peerstr))

    def unregister_user_to_rpi(self, client, rpi):
        if rpi is None:
            return
        if client in self.rpi_clients_registered_users[rpi.mac]:
            self.rpi_clients_registered_users[rpi.mac].remove(client)
            if self.debug:
                log.msg('RPISocketServerFactory.unregister_user_to_rpi rpi:%s user:%s' %
                        (rpi.mac, client.protocol.peerstr))
        if len(self.rpi_clients_registered_users[rpi.mac]) == 0:
            # Pause streaming
            rpi.pause_streaming()

    def get_rpi(self, rpi_mac):
        if rpi_mac in self.rpi_clients:
            return self.rpi_clients[rpi_mac]
        return None

    def notify_clients_rpi_state_change(self, rpi, state='offline'):
        for peerstr, user in self.user_client.iteritems():
            user.notifyRPIState(rpi, state)

    def register_user(self, user):
        if user.protocol.peerstr not in self.user_client:
            self.user_client[user.protocol.peerstr] = user
            if self.debug:
                log.msg('RPISocketServerFactory.register_user %s' % user.protocol.peerstr)

    def disconnect_user(self, user):
        if self.debug:
            log.msg('RPISocketServerFactory.disconnect_user %s' % user.protocol.peerstr)
        del self.user_client[user.protocol.peerstr]
        self.unregister_user_to_rpi(user, user.associated_rpi)

    def register_rpi(self, rpi):
        # this is called when the RPI has been authenticated with the WS server
        # register on the site server
        reactor.callInThread(self.sitecomm.register_rpi, rpi)
        # register locally to the factory
        self.rpi_clients[rpi.mac] = rpi
        self.rpi_clients_registered_users[rpi.mac] = []
        if self.debug:
            log.msg("RPISocketServerFactory.register_rpi - %s registered, %d rpi" % (rpi.mac, len(self.rpi_clients)))

    def register_rpi_wsite(self, rpi):
        # this is called when the RPI has been registers on the website
        self.notify_clients_rpi_state_change(rpi, state='online')

    def disconnect_rpi(self, rpi):
        if hasattr(rpi, 'mac'):
            if self.debug:
                log.msg("RPISocketServerFactory.disconnect_rpi - %s rpi disconnected" % (rpi.mac,))
            reactor.callInThread(self.sitecomm.disconnect_rpi, rpi)
            del self.rpi_clients[rpi.mac]
            del self.rpi_clients_registered_users[rpi.mac]

    def disconnect_rpi_wsite(self, rpi):
        # this is called after the RPI disconnect has been notified to the web server
        self.notify_clients_rpi_state_change(rpi, state='offline')

    def config_rpi(self, configs):
        """
        Not thread safe

        configs:
            dict with the following keys:
                'read': lst of port configs
                'write: lst of port configs
                'mac':  '00:00:...'
            port config dict with the following keys:
                'ch_port':  integer or boolean (check cls req)
                'equation': empty, or python style math
                'cls_name': class name as string, ex) 'ADC'

        Return: True/False for success
        """
        # check if RPI is actually an active client
        mac = configs['mac']
        if mac not in self.rpi_clients:
            return False

        rpi_client = self.rpi_clients[mac]
        # notify any listening users of RPI config change
        self.notify_clients_rpi_state_change(rpi_client, state='config')
        return rpi_client.config_io(reads=configs['read'], writes=configs['write'])


class FlashSocketPolicyServerProtocol(twistedsockets.Protocol):
    """
    Flash Socket Policy for web-socket-js fallback
    http://www.adobe.com/devnet/flashplayer/articles/socket_policy_files.html
    """
    def connectionMade(self):
        policy = '<?xml version="1.0"?><!DOCTYPE cross-domain-policy SYSTEM '\
                 '"http://www.macromedia.com/xml/dtds/cross-domain-policy.dtd">'\
                 '<cross-domain-policy><allow-access-from domain="*" to-ports="*" /></cross-domain-policy>'
        self.transport.write(policy)
        self.transport.loseConnection()
