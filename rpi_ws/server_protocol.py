from twisted.python import log
from twisted.internet import reactor
import twisted.internet.protocol as twistedsockets
from autobahn.websocket import WebSocketServerFactory, WebSocketServerProtocol, HttpException
import autobahn.httpstatus as httpstatus
from rpi_ws.server.client import UserClient, RPIClient

import settings


#"""
#User client related protocol
#"""

#"""
#RPI client related protocol and states
#"""


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
            '/': user,
            '/rpi/': rpi,
            '/rpi': rpi,
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
        WebSocketServerFactory.__init__(self,*args, **kwargs)

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
        client.unregister_to_rpi()
        if rpi is None:
            return
        if rpi.mac in self.rpi_clients_registered_users:
            if client in self.rpi_clients_registered_users[rpi.mac]:
                self.rpi_clients_registered_users[rpi.mac].remove(client)
                if self.debug:
                    log.msg('RPISocketServerFactory.unregister_user_to_rpi rpi:%s user:%s' %
                            (rpi.mac, client.protocol.peerstr))
        if rpi.mac not in self.rpi_clients_registered_users or len(self.rpi_clients_registered_users[rpi.mac]) == 0:
            # Pause streaming
            rpi.pause_streaming()

    def rpi_new_data_event(self, rpi):
        # resume streaming on any RPIs waiting for new data
        for client in self.rpi_clients_registered_users[rpi.mac]:
            client.resume_streaming()

    def copy_rpi_buffers(self, rpi, read_buffer, write_buffer):
        rpi.copy_buffers(read_buffer, write_buffer)

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
                log.msg("RPISocketServerFactory.disconnect_rpi - %s rpi disconnected" % rpi.mac)

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

        rpi_client = self.get_rpi(mac)
        if rpi_client is None:
            return False

        return rpi_client.config_io(reads=configs['read'], writes=configs['write'])


class FlashSocketPolicyServerProtocol(twistedsockets.Protocol):
    """
    Flash Socket Policy for web-socket-js fallback
    http://www.adobe.com/devnet/flashplayer/articles/socket_policy_files.html
    """

    def connectionMade(self):
        policy = '<?xml version="1.0"?><!DOCTYPE cross-domain-policy SYSTEM ' \
                 '"http://www.macromedia.com/xml/dtds/cross-domain-policy.dtd">' \
                 '<cross-domain-policy><allow-access-from domain="*" to-ports="*" /></cross-domain-policy>'
        self.transport.write(policy)
        self.transport.loseConnection()
