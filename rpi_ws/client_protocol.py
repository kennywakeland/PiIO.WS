from twisted.internet import reactor
from twisted.python import log
from autobahn.websocket import WebSocketClientProtocol, WebSocketClientFactory
from twisted.internet.protocol import ReconnectingClientFactory
import json
from hashlib import sha1
import hmac
import binascii

import rpi_data.interface as interface
import rpi_data.utility
import settings
import common_protocol
import buffer


class StreamState(common_protocol.State):
    def __init__(self, protocol, reads, writes):
        # reads/writes look like this
        # {u'cls:ADC, port:3': {'equations': [u'zzzz', u'asdfadfad'], 'obj': <rpi_data.interface.ADC object at 0x036D18D0>}}
        super(StreamState, self).__init__(protocol)

        self.config_reads = reads
        self.config_writes = writes

        self.polldata_read = buffer.UpdateDict()
        self.polldata_write = buffer.UpdateDict()

        self.ackcount = 0
        self.paused = True

    def onMessage(self, msg):
        msg = json.loads(msg)

        if msg['cmd'] == common_protocol.ServerCommands.DROP_TO_CONFIG:
            # wrong state, drop
            # flush IO
            io_clss = interface.get_interface_desc()
            for cls in io_clss['read']:
                cls.flush()
            for cls in io_clss['write']:
                cls.flush()
            self.protocol.pop_state()

            resp_msg = {'cmd': common_protocol.RPIClientCommands.DROP_TO_CONFIG_OK}
            self.sendJsonMessage(resp_msg)
            return

        elif msg['cmd'] == common_protocol.ServerCommands.ACK_DATA:
            server_ackcount = msg['ack_count']
            self.ackcount += server_ackcount
            if self.ackcount > -10:
                self.poll_and_send()

        elif msg['cmd'] == common_protocol.ServerCommands.RESUME_STREAMING:
            self.resume_streaming()

        elif msg['cmd'] == common_protocol.ServerCommands.PAUSE_STREAMING:
            self.pause_streaming()

        elif msg['cmd'] == common_protocol.ServerCommands.WRITE_DATA:
            key = msg['inter_face_port']
            value = msg['value']
            self.write_to_inter_face(key, value)

    def write_to_inter_face(self, inter_face_port, value):
        if inter_face_port not in self.config_writes or self.config_writes[inter_face_port]['obj'] is None:
            return
        self.config_writes[inter_face_port]['obj'].write(value)

    def poll_and_send(self):
        if self.ackcount <= -10 or self.paused:
            return

        for key, value in self.config_reads.iteritems():
            if value['obj'] is not None:
                self.polldata_read[key] = value['obj'].read()
            else:
                #log.err("value['obj'] is None ")
                self.polldata_write[key] = None

        for key, value in self.config_writes.iteritems():
            if value['obj'] is not None:
                self.polldata_write[key] = value['obj'].read()
            else:
                #log.err("value['obj'] is None ")
                self.polldata_write[key] = None

        if len(self.polldata_read) > 0 or len(self.polldata_write) > 0:
            msg = {'cmd': common_protocol.RPIClientCommands.DATA,
                   'read': self.polldata_read,
                   'write': self.polldata_write
                  }

            self.ackcount -= 1
            self.sendJsonMessage(msg)


        reactor.callLater(0, self.poll_and_send)

    def pause_streaming(self):
        self.paused = True

    def resume_streaming(self):
        self.paused = False
        self.poll_and_send()


class Config_Register_State(common_protocol.State):
    """
    Responsible for setting up the IO
    """

    def __init__(self, protocol):
        super(Config_Register_State, self).__init__(protocol)
        self.hmac_reply_expected = False
        self._send_desc()

    def onMessage(self, msg):
        msg = json.loads(msg)

        if msg['cmd'] == common_protocol.ServerCommands.AUTH:
            self.token = msg['payload']['token']
            if self.protocol.factory.debug:
                log.msg("%s.onMessage - Received token %s" % (self.__class__.__name__, self.token))

            # compute HMAC reply
            hashed = hmac.new(settings.HMAC_TOKEN, self.protocol.mac + self.token, sha1)
            self.hamc_token = binascii.b2a_base64(hashed.digest())[:-1]
            reply_msg = {'cmd': common_protocol.ServerCommands.AUTH,
                         'payload': {'token': self.hamc_token}}
            self.sendJsonMessage(reply_msg)
            self.hmac_reply_expected = True
            return

        if self.hmac_reply_expected and msg['cmd'] == common_protocol.ServerCommands.ACK:
            if self.protocol.factory.debug:
                log.msg("RegisterState.onMessage - Registration Ack")

        if msg['cmd'] == common_protocol.ServerCommands.CONFIG:
            read_settings = msg['payload']['read']
            writes_settings = msg['payload']['write']

            if self.protocol.factory.debug:
                log.msg("ConfigState.onMessage - Received configs, %d reads, %d writes"
                        % (len(read_settings), len(writes_settings)))

            # attempt to configure IO.......
            self.instantiate_io(read_settings)
            self.instantiate_io(writes_settings)

            if self.protocol.factory.debug:
                log.msg('ConfigState - Instantiated %d read interfaces' % len(read_settings))
                log.msg('ConfigState - Instantiated %d write interfaces' % len(writes_settings))

            self.protocol.push_state(StreamState(self.protocol, reads=read_settings, writes=writes_settings))

            # there should be some feedback done here if something fails
            msg = {'cmd': common_protocol.RPIClientCommands.CONFIG_OK}
            self.sendJsonMessage(msg)

    def instantiate_io(self, io_collection):
        # instantiate interface instances
        # {u'cls:ADC, port:3': {'cls_name':'ADC', 'ch_port':3, 'equations': [u'dddd', u'']}}
        # to:
        # {u'cls:ADC, port:3': {'cls_name':'ADC', 'ch_port':3, 'equations': [u'dddd', u''], 'obj':<instance>}}
        for key, value in io_collection.iteritems():
            cls_str = value['cls_name']
            ch_port = value['ch_port']
            if self.protocol.factory.debug:
                log.msg('ConfigState - Configuring module %s on ch/port %s' % (cls_str, ch_port))

            cls = getattr(interface, cls_str)

            try:
                instance = cls(ch_port)
                value['obj'] = instance
            except Exception, ex:
                if self.protocol.factory.debug:
                    log.err('ConfigState - Ex creating module %s' % str(ex))
                value['obj'] = None
                #raise
                continue

    def _send_desc(self):
        desc = {'inter_face': {},
                'mac': self.protocol.mac}

        def inter_desc(inter_faces):
            # list of classes
            ret = []
            for cls in inter_faces:
                name = cls.__name__
                desc = rpi_data.utility.trim(cls.__doc__)
                choices = []

                for choice_key, choice_value in cls.IO_CHOICES:
                    choice = {'s': choice_key,
                              'd': choice_value}
                    choices.append(choice)

                ret.append({'name': name, 'desc': desc, 'choices': choices, 'io_type': cls.IO_TYPE})
            return ret

        for key in self.protocol.interfaces.iterkeys():
            desc['inter_face'][key] = inter_desc(self.protocol.interfaces[key])

        self.sendJsonMessage(desc)


class RPIClientProtocol(WebSocketClientProtocol, common_protocol.ProtocolState):
    def __init__(self):
        common_protocol.ProtocolState.__init__(self)
        self.mac = rpi_data.utility.get_mac()
        self.interfaces = interface.get_interface_desc()

    def onOpen(self):
        # push the initial state
        self.push_state(Config_Register_State(self))

    def onMessage(self, msg, binary):
        try:
            state = self.state_stack.pop_wr()
            state.onMessage(msg)
        except IndexError, e:
            if self.factory.debug:
                log.err("%s.onMessage - Received a message in an unknown state, ignored %s" % (self.__class__.__name__, e))


class ReconnectingWebSocketClientFactory(ReconnectingClientFactory, WebSocketClientFactory):
    maxDelay = 30