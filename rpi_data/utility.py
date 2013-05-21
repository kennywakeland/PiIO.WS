import sys
import uuid
from time import sleep, time


def get_mac():
    """
    Returns hardware mac address in FF:FF:FF:FF:FF:FF string format
    """
    mac_int = uuid.getnode()
    mac_str = hex(mac_int)[2:].zfill(12).upper()
    mac = ':'.join([mac_str[i:i + 2] for i in xrange(0, 12, 2)])
    return mac


def trim(docstring):
    if not docstring:
        return ''
        # Convert tabs to spaces (following the normal Python rules)
    # and split into a list of lines:
    lines = docstring.expandtabs().splitlines()
    # Determine minimum indentation (first line doesn't count):
    indent = sys.maxint
    for line in lines[1:]:
        stripped = line.lstrip()
        if stripped:
            indent = min(indent, len(line) - len(stripped))
        # Remove indentation (first line is special):
    trimmed = [lines[0].strip()]
    if indent < sys.maxint:
        for line in lines[1:]:
            trimmed.append(line[indent:].rstrip())
        # Strip off trailing and leading blank lines:
    while trimmed and not trimmed[-1]:
        trimmed.pop()
    while trimmed and not trimmed[0]:
        trimmed.pop(0)
        # Return a single string:
    return '\n'.join(trimmed)

try:
    import smbus

    class Relay:
        def __init__(self, address, number_of_relay=4):
            self.address = address
            self.bus = smbus.SMBus(1)

            self.number_of_relay = number_of_relay
            self.relay_state = [0 for x in range(self.number_of_relay)]
            self.write()

        def write(self):
            state_boll = int("0000"+"".join(str(x) for x in self.relay_state), 2)
            sleep(0.2)
            self.bus.write_byte_data(self.address, 0x10, state_boll)

        def read(self):
            pass

        def get_state(self, relay_number):
            relay_number = int(relay_number)

            if relay_number <= 0 or relay_number > self.number_of_relay:
                return -1
            else:
                return self.relay_state[relay_number-1]

        def set_state(self, relay_number, relay_state, no_write=False):
            relay_number = int(relay_number)
            if relay_number <= 0 or relay_number > self.number_of_relay or relay_state < 0 or relay_state > 1:
                return -1
            elif relay_state == self.relay_state[relay_number-1]:
                return 2
            else:
                self.relay_state[relay_number-1] = relay_state

                if no_write:
                    return 0
                else:
                    self.write()
                    return 1

    RELAY = Relay(0x58)

    class DigitalInput:
        def __init__(self, address, number_of_input=4):
            self.address = address
            self.bus = smbus.SMBus(1)
            self.number_of_input = number_of_input
            self.input_state = "0"*self.number_of_input
            self.update_time_min = 20

            self.set_update_time()

        def write(self):
            pass

        def read(self):
            if self.can_update():
                sleep(0.2)
                input_state = "{0:b}".format(int(self.bus.read_byte_data(self.address, 0x20)))
                add_zero = self.number_of_input - len(input_state)
                self.input_state = "%s%s" % ("0" * add_zero, input_state)
                self.set_update_time()


        def get_state(self, input_number):
            input_number = int(input_number)

            if input_number < 0 or input_number > self.number_of_input:
                return -1
            else:
                self.read()
                return self.input_state[input_number]

        def get_time(self):
            return int(round(time() * 1000))

        def can_update(self):
            return (self.get_time() - self.update_time) > self.update_time_min

        def set_update_time(self):
            self.update_time = self.get_time()

    DIGITAL_INPUT = DigitalInput(0x58)

except ImportError as exc:
    RELAY = []
