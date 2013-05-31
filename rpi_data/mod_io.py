__author__ = 'kenny'

from time import sleep, time
from twisted.python import log
import smbus


class MOD_IO(object):
    def __init__(self, address, number_of_ports):
        self.address = address
        self.number_of_ports = number_of_ports

        self.bus = smbus.SMBus(1)

        self.update_time_min = 0.4
        self.sleep_time = 0.02
        self.set_update_time()

        self.ports_state = [0 for x in range(self.number_of_ports)]

    def write(self):
        pass

    def read(self):
        pass

    def get_state(self, port_number):
        self.read()
        try:
            return self.ports_state[port_number]
        except IndexError, e:
            log.err("%s.get_state  '%s' = %s index %s" % (self.__class__.__name__, self.ports_state,port_number, e))
            return -1

    def set_state(self, port_number, port_state):
        pass

    def get_time(self):
        return time()

    def can_update(self):
        if (self.get_time() - self.update_time) > self.update_time_min:
            return True
        else:
            sleep(0.001)
            return False

    def set_update_time(self):
        self.update_time = self.get_time()



class MOD_IO_Relay(MOD_IO):
    def __init__(self, address, number_of_ports=4):
        super(MOD_IO_Relay, self).__init__(address, number_of_ports)
        self.write()

    def write(self):
        sleep(self.sleep_time)
        state_boll = int("0000" + "".join(str(x) for x in self.ports_state), 2)
        self.bus.write_byte_data(self.address, 0x10, state_boll)

    def set_state(self, port_number, port_state):
        port_number = int(port_number)

        if port_state < 0 or port_state > 1:
            return -1
        elif port_state == self.ports_state[port_number]:
            return 2
        else:
            self.ports_state[port_number] = port_state
            self.write()
            return 1


MOD_IO_RELAY = MOD_IO_Relay(0x58)


class MOD_IO_DigitalInput(MOD_IO):
    def __init__(self, address, number_of_ports=4):
        super(MOD_IO_DigitalInput, self).__init__(address, number_of_ports)
        self.ports_state = "0" * self.number_of_ports

    def read(self):
        if self.can_update():
            sleep(self.sleep_time)
            input_state = "{0:b}".format(int(self.bus.read_byte_data(self.address, 0x20)))
            add_zero = self.number_of_ports - len(input_state)
            self.ports_state = "%s%s" % ("0" * add_zero, input_state)
            self.set_update_time()


MOD_IO_DIGITAL_INPUT = MOD_IO_DigitalInput(0x58)


class MOD_IO_AnalogueInput(MOD_IO):
    def __init__(self, address, number_of_ports=4):
        super(MOD_IO_AnalogueInput, self).__init__(address, number_of_ports)
        # save time regenerating range
        self.number_range = range(self.number_of_ports)

    def read(self):
        if self.can_update():
            for i in self.number_range:
                sleep(self.sleep_time)
                self.ports_state[i] = self.bus.read_word_data(self.address, 0x30 + i)

            #print " self.ports_state ", self.ports_state
            self.set_update_time()


MOD_IO_Analogue_INPUT = MOD_IO_AnalogueInput(0x58)


#try:
#except ImportError as exc: