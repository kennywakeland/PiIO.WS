import json
from twisted.internet import reactor
from twisted.python import log
from rpi_ws import common_protocol, buffer
from rpi_ws.server.state import RPIRegisterState, RPIStreamState, RPIConfigState

__author__ = 'kenny'


class Client(common_protocol.ProtocolState):
    def __init__(self, protocol):
        super(Client, self).__init__()

        self.protocol = protocol

    def onMessage(self, msg):
        try:
            state = self.state_stack.pop_wr()
            state.onMessage(msg)
        except IndexError, e:
            if self.protocol.factory.debug:
                log.err("%s.onMessage - Received a message in an unknown state, ignored %s" % (self.__class__.__name__, e))

    def onClose(self, wasClean, code, reason):
        pass

    def onOpen(self):
        pass


class UserClient(Client):
    def __init__(self, protocol):
        super(UserClient, self).__init__(protocol)
        self.associated_rpi = None
        self.streaming_buffer_read = None
        self.streaming_buffer_write = None
        self.ackcount = 0
        self.paused = True

    def register_to_rpi(self, rpi_mac):
        # notify factory we want to unregister if registered first
        self.ackcount = 0
        if self.associated_rpi is not None:
            self.protocol.factory.unregister_user_to_rpi(self, self.associated_rpi)
        rpi = self.protocol.factory.get_rpi(rpi_mac)
        if rpi:
            self.streaming_buffer_read = buffer.UpdateDict()
            self.streaming_buffer_write = buffer.UpdateDict()
            self.associated_rpi = rpi
            self.protocol.factory.register_user_to_rpi(self, self.associated_rpi)
            # begin streaming
            self.resume_streaming()

    def resume_streaming(self):
        self.paused = False
        self.copy_and_send()

    def pause_streaming(self):
        self.paused = True

    def copy_and_send(self):
        if self.ackcount <= -10 or self.paused:
            return

        # copy buffers
        self.protocol.factory.copy_rpi_buffers(self.associated_rpi,
                                               self.streaming_buffer_read,
                                               self.streaming_buffer_write)

        if len(self.streaming_buffer_read) > 0 or len(self.streaming_buffer_write) > 0:
            msg = {'cmd': common_protocol.ServerCommands.WRITE_DATA,
                   'read': self.streaming_buffer_read,
                   'write': self.streaming_buffer_write}
            self.ackcount -= 1
            self.protocol.sendMessage(json.dumps(msg))
            # keep polling until we run out of data
            reactor.callLater(0, self.copy_and_send)
        else:
            # when there's new data resume will be called
            self.pause_streaming()

    def unregister_to_rpi(self):
        self.pause_streaming()
        if self.associated_rpi is not None:
            self.associated_rpi = None

    def notifyRPIState(self, rpi, state):
        if state == 'config':
            if self.associated_rpi is not rpi:
                return
        msg = {'cmd': common_protocol.ServerCommands.RPI_STATE_CHANGE,
               'rpi_mac': rpi.mac,
               'rpi_state': state}
        self.protocol.sendMessage(json.dumps(msg))

    def onMessage(self, msg):
        try:
            msg = json.loads(msg)
        except:
            if self.protocol.debug:
                log.err('UserState.onMessage - JSON error, dropping')
            self.protocol.failConnection()

        if msg['cmd'] == common_protocol.UserClientCommands.CONNECT_RPI:
            mac = msg['rpi_mac']
            self.register_to_rpi(mac)

        elif msg['cmd'] == common_protocol.UserClientCommands.ACK_DATA:
            ackcount = msg['ack_count']
            self.ackcount += ackcount
            if self.ackcount > -10:
                self.copy_and_send()

        elif msg['cmd'] == common_protocol.UserClientCommands.WRITE_DATA:
            port = msg['inter_face_port']
            value = msg['value']
            if self.associated_rpi is not None:
                self.associated_rpi.write_interface_data(port, value)

    def onClose(self, wasClean, code, reason):
        self.protocol.factory.disconnect_user(self)

    def onOpen(self):
        self.protocol.factory.register_user(self)


class RPIClient(Client):
    def __init__(self, protocol):
        super(RPIClient, self).__init__(protocol)

    def onClose(self, wasClean, code, reason):
        # if we're registered remove ourselves from active client list
        if hasattr(self, 'mac'):
            self.protocol.factory.disconnect_rpi(self)

    def onOpen(self):
        self.push_state(RPIRegisterState(self))

    def copy_buffers(self, read_buffer, write_buffer):
        try:
            state = self.current_state()
        except IndexError:
            # RPI has no states
            return False
        if isinstance(state, RPIStreamState):
            for key, value in state.read_data_buffer_eq.iteritems():
                read_buffer[key] = value
            for key, value in state.write_data_buffer_eq.iteritems():
                write_buffer[key] = value

            return True
        return False

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

    def write_interface_data(self, key, data):
        try:
            state = self.current_state()
        except IndexError:
            # RPI has no states
            return False

        if isinstance(state, RPIStreamState):
            state.write_interface_data(key, data)
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