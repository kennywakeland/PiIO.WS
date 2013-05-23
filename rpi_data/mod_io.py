__author__ = 'kenny'

from time import sleep, time
import smbus


class MOD_IO(object):
    def __init__(self, address, number_of_ports):
        self.address = address
        self.number_of_ports = number_of_ports

        self.bus = smbus.SMBus(1)

        self.update_time_min = 20
        self.set_update_time()

        self.ports_state = [0 for x in range(self.number_of_ports)]

    def write(self):
        pass

    def read(self):
        pass

    def get_state(self, port_number):
        port_number = int(port_number)

        if port_number < 0 or port_number > self.number_of_ports:
            return -1
        else:
            self.read()
            port_index = self.get_index_id(port_number)
            return self.ports_state[port_index]

    def set_state(self, port_number, port_state, write=True):
        pass

    def get_time(self):
        return int(round(time() * 1000))

    def can_update(self):
        return (self.get_time() - self.update_time) > self.update_time_min

    def set_update_time(self):
        self.update_time = self.get_time()

    def get_index_id(self, port_number):
        return self.number_of_ports - port_number


class MOD_IO_Relay(MOD_IO):
    def __init__(self, address, number_of_ports=4):
        super(MOD_IO_Relay, self).__init__(address, number_of_ports)
        self.write()

    def write(self):
        state_boll = int("0000" + "".join(str(x) for x in self.ports_state), 2)
        sleep(0.2)
        self.bus.write_byte_data(self.address, 0x10, state_boll)

    def set_state(self, port_number, port_state, write=True):
        port_number = int(port_number)
        port_index = self.get_index_id(port_number)

        if port_index < 0 or port_index > self.number_of_ports or port_state < 0 or port_state > 1:
            return -1
        elif port_state == self.ports_state[port_index]:
            return 2
        else:
            self.ports_state[port_index] = port_index

            if write:
                self.write()
            return 1


MOD_IO_RELAY = MOD_IO_Relay(0x58)


class MOD_IO_DigitalInput(MOD_IO):
    def __init__(self, address, number_of_ports=4):
        super(MOD_IO_DigitalInput, self).__init__(address, number_of_ports)
        self.ports_state = "0" * self.number_of_ports

    def read(self):
        if self.can_update():
            sleep(0.2)
            input_state = "{0:b}".format(int(self.bus.read_byte_data(self.address, 0x20)))
            add_zero = self.number_of_ports - len(input_state)
            self.ports_state = "%s%s" % ("0" * add_zero, input_state)
            self.set_update_time()


MOD_IO_DIGITAL_INPUT = MOD_IO_DigitalInput(0x58)

#try:
#except ImportError as exc: