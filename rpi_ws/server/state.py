import binascii
import hashlib
from hashlib import sha1
import hmac
import json
import os
import time
from twisted.python import log
from rpi_ws import common_protocol, settings


class ServerState(common_protocol.State):
    def __init__(self, client):
        self.client = client

    def activated(self):
        if self.client.protocol.debug:
            log.msg("%s.activated()" % self.__class__.__name__)

    def deactivated(self):
        if self.client.protocol.debug:
            log.msg("%s.deactivated()" % self.__class__.__name__)


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
            self.client.inter_face = parsed['inter_face']
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
                self.client.protocol.sendMessage(json.dumps({'cmd': common_protocol.ServerCommands.ACK}))
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
        msg = {'cmd': common_protocol.ServerCommands.AUTH, 'payload': {'token': self.rand_token}}
        self.client.protocol.sendMessage(json.dumps(msg))


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
                log.err('RPIConfigState - RPI failed to configure')
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
                    io_new_dict = {'cls_name': cls_str,
                                   'ch_port': ch_port,
                                   'equations': [equation]}
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

        msg = {'cmd': common_protocol.ServerCommands.CONFIG,
               'payload': {'read': self.config_reads, 'write': self.config_writes}}

        self.client.protocol.sendMessage(json.dumps(msg))


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
        self.write_data_buffer_eq = {}
        self.write_data_eq_map = {}
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
        self.client.protocol.factory.notify_clients_rpi_state_change(self.client, state='drop_stream')

    def activated(self):
        super(RPIStreamState, self).deactivated()
        self.client.protocol.factory.notify_clients_rpi_state_change(self.client, state='stream')

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
                        new_key = 'cls:%s, port:%s, eq:%s' % (
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
                # equations for write interfaces are applied on the returned value
                # input value to interfaces are unchanged
                self.write_data_buffer[key] = value
                # key: 'cls:%s, port:%d, eq:%s'
                if key in self.config_writes:
                    for eq in self.config_writes[key]['equations']:
                        new_key = 'cls:%s, port:%s, eq:%s' % (
                            self.config_writes[key]['cls_name'],
                            self.config_writes[key]['ch_port'],
                            eq,
                        )
                        self.write_data_eq_map[new_key] = key
                        self.write_data_buffer_eq[new_key] = {
                            'calculated': self.evaluate_eq(eq, value),
                            'real': value,
                        }
                else:
                    # TODO: drop to config state or something, remote config seems to be invalid
                    pass
                    # notify factory to update listening clients
            if self.datamsgcount_ack >= 5:
                msg = {'cmd': common_protocol.ServerCommands.ACK_DATA, 'ack_count': self.datamsgcount_ack}
                self.client.protocol.sendMessage(json.dumps(msg))
                self.datamsgcount_ack = 0
                # notify factory of new data event
            self.client.protocol.factory.rpi_new_data_event(self.client)

    def resume_streaming(self):
        msg = {'cmd': common_protocol.ServerCommands.RESUME_STREAMING}
        self.client.protocol.sendMessage(json.dumps(msg))

    def pause_streaming(self):
        msg = {'cmd': common_protocol.ServerCommands.PAUSE_STREAMING}
        self.client.protocol.sendMessage(json.dumps(msg))

    def write_interface_data(self, key, value):
        # removes the EQ from the key sent by the client
        config_key = self.write_data_eq_map[key]
        msg = {'cmd': common_protocol.ServerCommands.WRITE_DATA,
               'inter_face_port': config_key,
               'value': value}
        self.client.protocol.sendMessage(json.dumps(msg))

    def drop_to_config(self, reads, writes):
        # drop remote RPI to config state
        msg = {'cmd': common_protocol.ServerCommands.DROP_TO_CONFIG}
        self.client.protocol.sendMessage(json.dumps(msg))
        self.delegate_config_reads = reads
        self.delegate_config_writes = writes