"""All the settings in one place. Edit them here, not in the other files."""

# The relay board connects TO this PC (the board is the TCP client)
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 8800

# The other program (auto-mode distances) connects here
INPUT_HOST = "127.0.0.1"   # "0.0.0.0" = accept connections from other PCs too
INPUT_PORT = 8801

# Actuators
TRAVEL_SPEED = 10.0        # mm/s
MAX_STROKE_MM = 100.0      # TODO: set to your actuator's real stroke length
NUM_ACTUATORS = 6
FIRST_COIL = 4             # relay channel 5 = coil address 4 (channel N = coil N-1)
