import os
from openpilot.common.basedir import BASEDIR

MAPD_BIN_DIR = os.path.join(BASEDIR, 'openpilot', 'third_party/mapd_pfeiferj')
MAPD_PATH = os.path.join(MAPD_BIN_DIR, 'mapd')

# mapd still runs (OSM downloads) but has no position source since ubloxd owns LastGPSPosition;
# map-based features stay off until mapd is rebuilt
MAPD_AVAILABLE = False
# HL-FIX(map-speed-limit): this fork still feeds mapd a position (osm_map_data.py writes the JSON
# fix to the /dev/shm mem store as bytes, which never collides with ubloxd's persistent BYTES copy),
# so the resolver's map SPEED-LIMIT source keeps working. SCC-Map stays gated by MAPD_AVAILABLE above.
MAPD_SPEED_LIMIT_AVAILABLE = True
