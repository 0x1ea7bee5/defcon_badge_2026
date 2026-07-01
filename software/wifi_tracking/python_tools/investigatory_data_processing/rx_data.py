"""
rx_data.py — Serial receiver and thread-safe data store.

Parses the TELEM: wire protocol from the ESP32 and stores CBF/CSI
frames in efficient rolling deques.  DSP is left to dsp.py.

Wire format:
    TELEM:<hex>\\n
    hex encodes: MAGIC(4B) LEN(2B LE) PAYLOAD(N B) XOR_CKSUM(1B)

CBF magic: CB F1 FE ED
CSI magic: C5 1D FE ED
"""

import struct
import threading
import time
from collections import deque

import numpy as np

# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

_MAGIC_CBF   = bytes([0xCB, 0xF1, 0xFE, 0xED])
_MAGIC_CSI   = bytes([0xC5, 0x1D, 0xFE, 0xED])
_PREFIX      = b'TELEM:'

WINDOW_LEN   = 200   # rolling history depth (frames per MAC)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def mac_str(raw: bytes) -> str:
    """Format 6-byte MAC address as colon-separated hex string.

    Args:
        raw (bytes): 6 raw MAC bytes.
    Returns:
        str: e.g. 'AA:BB:CC:DD:EE:FF'.
    """
    return ':'.join(f'{b:02X}' for b in raw)


# ------------------------------------------------------------------
# Packet parsing
# ------------------------------------------------------------------

def _try_parse_line(line: bytes):
    """Parse one TELEM: hex line.

    Args:
        line (bytes): Raw serial line.
    Returns:
        tuple: ('cbf'|'csi', dict) or None on failure.
    """
    line = line.strip()
    if not line.startswith(_PREFIX):
        return None
    try:
        data = bytes.fromhex(line[len(_PREFIX):].decode('ascii'))
    except Exception:
        return None

    if len(data) < 7:
        return None

    magic = data[:4]
    if magic not in (_MAGIC_CBF, _MAGIC_CSI):
        return None

    (length,) = struct.unpack_from('<H', data, 4)
    if len(data) < 6 + length + 1:
        return None

    payload  = data[6:6 + length]
    cksum_rx = data[6 + length]

    cksum_calc = 0
    for b in payload:
        cksum_calc ^= b
    if cksum_calc != cksum_rx:
        print('[rx] checksum mismatch — dropped', flush=True)
        return None

    if magic == _MAGIC_CBF:
        pkt = _parse_cbf(payload)
        return ('cbf', pkt) if pkt is not None else None
    pkt = _parse_csi(payload)
    return ('csi', pkt) if pkt is not None else None


def _parse_cbf(payload: bytes) -> dict:
    """Parse CBF payload into a dict with v_all tensor.

    Args:
        payload (bytes): Raw CBF payload bytes.
    Returns:
        dict: CBF frame or None on parse error.
            mac (str), nc (int), nr (int), n_sc (int),
            phy_type (int), is_mu (bool), bandwidth (int),
            snr (list[int]),
            v_all (np.ndarray): shape (n_sc, nr, nc) complex128.
    """
    try:
        pos = 0
        mac        = mac_str(payload[pos:pos + 6]); pos += 6
        nc         = payload[pos];                  pos += 1
        nr         = payload[pos];                  pos += 1
        (n_sc,)    = struct.unpack_from('<H', payload, pos); pos += 2
        phy_type   = payload[pos]; pos += 1
        is_mu      = bool(payload[pos]); pos += 1
        bandwidth  = payload[pos]; pos += 1
        snr = list(struct.unpack_from(f'{nc}b', payload, pos))
        pos += nc

        phi_count = nc * nr - nc * (nc + 1) // 2
        psi_count = phi_count

        v_all = np.empty((n_sc, nr, nc), dtype=complex)
        for k in range(n_sc):
            pos += phi_count * 2   # skip phi
            pos += psi_count * 2   # skip psi
            v_r = np.array(
                struct.unpack_from(f'<{nr * nc}f', payload, pos)
            ).reshape(nr, nc)
            pos += nr * nc * 4
            v_i = np.array(
                struct.unpack_from(f'<{nr * nc}f', payload, pos)
            ).reshape(nr, nc)
            pos += nr * nc * 4
            v_all[k] = v_r + 1j * v_i
    except Exception:
        return None

    return {
        'mac':      mac,
        'nc':       nc,
        'nr':       nr,
        'n_sc':     n_sc,
        'phy_type': phy_type,
        'is_mu':    is_mu,
        'bandwidth': bandwidth,
        'snr':      snr,
        'v_all':    v_all,
        'timestamp': time.time(),
    }


def _parse_csi(payload: bytes) -> dict:
    """Parse CSI payload into amplitude/phase arrays.

    Args:
        payload (bytes): Raw CSI payload bytes.
    Returns:
        dict: CSI frame or None on parse error.
            mac (str), rssi (int), channel (int),
            amp (np.ndarray): float, shape (n_sc,),
            phase (np.ndarray): float radians, shape (n_sc,).
    """
    try:
        pos = 0
        mac        = mac_str(payload[pos:pos + 6]); pos += 6
        pos       += 6   # skip dst_mac
        (rssi,)    = struct.unpack_from('b', payload, pos); pos += 1
        channel    = payload[pos]; pos += 1
        (n_samp,)  = struct.unpack_from('<H', payload, pos); pos += 2

        if pos + n_samp > len(payload):
            return None

        raw       = np.frombuffer(
            payload[pos:pos + n_samp], dtype=np.int8).astype(float)
        n_cplx    = len(raw) // 2
        iq        = raw[:n_cplx * 2].reshape(-1, 2)
        cplx      = iq[:, 0] + 1j * iq[:, 1]
    except Exception:
        return None

    return {
        'mac':       mac,
        'rssi':      rssi,
        'channel':   channel,
        'amp':       np.abs(cplx),
        'phase':     np.angle(cplx),
        'timestamp': time.time(),
    }


# ------------------------------------------------------------------
# Data Store
# ------------------------------------------------------------------

class DataStore:
    """Thread-safe rolling-window store for CBF and CSI frames.

    CBF frames are stored as raw v_all tensors (n_sc, nr, nc).
    CSI frames store amp and phase arrays.
    All per-MAC buffers are deques of length WINDOW_LEN.

    Listeners registered via add_listener(fn) are called with
    (kind, frame) on every ingested packet, while the lock is held.
    """

    def __init__(self, window: int = WINDOW_LEN):
        self.lock      = threading.Lock()
        self._window   = window
        self._listeners = []

        # Ordered MAC lists
        self.cbf_order = []
        self.csi_order = []

        # CBF: per-MAC metadata (latest values)
        self.cbf_meta  = {}   # mac -> {nr, nc, snr, phy_type, bandwidth}
        # CBF: per-MAC rolling frame history
        # Each frame: {v_all:(n_sc,nr,nc), n_sc, timestamp}
        self.cbf_frames = {}  # mac -> deque[frame]

        # CSI: per-MAC metadata
        self.csi_meta   = {}  # mac -> {rssi, channel}
        # CSI: per-MAC rolling frame history
        # Each frame: {amp, phase, rssi, channel, timestamp}
        self.csi_frames = {}  # mac -> deque[frame]

    def add_listener(self, fn):
        """Register fn(kind, frame) called on every ingested packet.

        Args:
            fn (callable): Called with ('cbf'|'csi', dict).
        """
        self._listeners.append(fn)

    def ingest_cbf(self, pkt: dict):
        """Store one CBF packet. Called with lock held.

        Args:
            pkt (dict): Parsed CBF frame from _parse_cbf().
        """
        mac = pkt['mac']
        if mac not in self.cbf_frames:
            self.cbf_order.append(mac)
            self.cbf_frames[mac] = deque(maxlen=self._window)

        self.cbf_meta[mac] = {
            'nr':        pkt['nr'],
            'nc':        pkt['nc'],
            'snr':       pkt['snr'],
            'phy_type':  pkt['phy_type'],
            'is_mu':     pkt['is_mu'],
            'bandwidth': pkt['bandwidth'],
        }
        self.cbf_frames[mac].append({
            'v_all':     pkt['v_all'],
            'n_sc':      pkt['n_sc'],
            'nr':        pkt['nr'],
            'nc':        pkt['nc'],
            'timestamp': pkt['timestamp'],
        })
        for fn in self._listeners:
            fn('cbf', pkt)

    def ingest_csi(self, pkt: dict):
        """Store one CSI packet. Called with lock held.

        Args:
            pkt (dict): Parsed CSI frame from _parse_csi().
        """
        mac = pkt['mac']
        if mac not in self.csi_frames:
            self.csi_order.append(mac)
            self.csi_frames[mac] = deque(maxlen=self._window)

        self.csi_meta[mac] = {
            'rssi':    pkt['rssi'],
            'channel': pkt['channel'],
        }
        self.csi_frames[mac].append(pkt)
        for fn in self._listeners:
            fn('csi', pkt)

    def get_cbf_v_all(self, mac: str) -> np.ndarray:
        """Return stacked v_all history for one MAC.

        Args:
            mac (str): MAC address string.
        Returns:
            np.ndarray: shape (n_frames, n_sc, nr, nc) complex, or None.
        """
        frames = self.cbf_frames.get(mac)
        if not frames:
            return None
        return np.stack([f['v_all'] for f in frames], axis=0)

    def get_csi_history(self, mac: str):
        """Return list of CSI frames for one MAC.

        Args:
            mac (str): MAC address string.
        Returns:
            list[dict]: CSI frame dicts, oldest first.
        """
        frames = self.csi_frames.get(mac)
        if not frames:
            return []
        return list(frames)

    @property
    def running(self) -> bool:
        """True while the serial reader is active."""
        return self._running

    _running = True

    def stop(self):
        """Signal the serial reader to stop."""
        self._running = False


# ------------------------------------------------------------------
# Serial Reader
# ------------------------------------------------------------------

class SerialReader:
    """Background thread that reads serial lines and feeds DataStore.

    Args:
        store (DataStore): Target data store.
        port (str): Serial port path (e.g. '/dev/ttyACM0').
        baud (int): Baud rate.
    """

    def __init__(self, store: DataStore, port: str, baud: int = 115200):
        self._store  = store
        self._port   = port
        self._baud   = baud
        self._thread = None

    def start(self):
        """Start background reader thread."""
        self._thread = threading.Thread(
            target=self._run, daemon=True, name='SerialReader')
        self._thread.start()

    def _run(self):
        import serial
        print(f'[rx] opening {self._port} @ {self._baud}', flush=True)
        while self._store._running:
            try:
                with serial.Serial(
                        self._port, self._baud, timeout=1) as ser:
                    while self._store._running:
                        line = ser.readline()
                        if not line:
                            continue
                        result = _try_parse_line(line)
                        if result is None:
                            continue
                        kind, pkt = result
                        with self._store.lock:
                            if kind == 'cbf':
                                self._store.ingest_cbf(pkt)
                            else:
                                self._store.ingest_csi(pkt)
                        print(
                            f'[rx] {kind.upper()} {pkt["mac"]}',
                            flush=True)
            except Exception as exc:
                print(f'[rx] {exc}', flush=True)
                time.sleep(1.0)
