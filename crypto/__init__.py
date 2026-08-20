import base64
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend


class NanaPacker:
    """TTS.Network.Packer 的 Python 复刻。

    wire format (实测确认): body = Base64( IV(16) || AES-256-CBC(key, IV, PKCS7(plaintext)) )
    —— IV 是每条消息随机生成并前置的，不是静态 pi。静态字段 pi/IV_LENGTH 可能用于
       其他用途（或为遗留）。故本类的 encode/decode 都按"IV 前置"处理。

    构造时仍可传 iv_hex 作为"固定 IV 兼容"用途，但默认走随机 IV 前置。
    """

    def __init__(self, key_hex=None, iv_hex=None, key_size=32):
        self.key = bytes.fromhex(key_hex) if key_hex else None
        # iv_hex 可选: 若提供则用固定 IV (兼容旧假设); 否则用随机 IV 前置
        self.fixed_iv = bytes.fromhex(iv_hex) if iv_hex else None

    @property
    def has_key(self):
        return self.key is not None

    def _pkcs7_pad(self, data, block_size=16):
        pad_len = block_size - (len(data) % block_size)
        return data + bytes([pad_len] * pad_len)

    def _pkcs7_unpad(self, data):
        if not data:
            raise ValueError("empty data")
        pad_len = data[-1]
        if pad_len > 16 or pad_len == 0:
            raise ValueError("Bad padding")
        for i in range(1, pad_len + 1):
            if data[-i] != pad_len:
                raise ValueError("Bad padding")
        return data[:-pad_len]

    # ── 原始字节级 (IV 前置) ───────────────────────────────
    def encode_bytes(self, raw_data):
        """加密: 生成随机 IV, 返回 IV(16) || AES-CBC-encrypt(key, IV, PKCS7(raw))"""
        iv = self.fixed_iv if self.fixed_iv is not None else __import__("os").urandom(16)
        cipher = Cipher(algorithms.AES(self.key), modes.CBC(iv), backend=default_backend())
        e = cipher.encryptor()
        ct = e.update(self._pkcs7_pad(raw_data)) + e.finalize()
        return iv + ct

    def decode_bytes(self, data):
        """解密: data = IV(16) || ct → 明文"""
        if len(data) < 32 or len(data) % 16 != 0:
            raise ValueError(f"bad ciphertext length {len(data)} (need >=32, mod16=0)")
        iv = data[:16]
        ct = data[16:]
        cipher = Cipher(algorithms.AES(self.key), modes.CBC(iv), backend=default_backend())
        d = cipher.decryptor()
        return self._pkcs7_unpad(d.update(ct) + d.finalize())

    # ── 字符串级 (Base64 外层, 匹配 Packer.EncodeString/DecodeString) ──
    def encode_string(self, raw_string):
        return base64.b64encode(self.encode_bytes(raw_string.encode("utf-8"))).decode("ascii")

    def decode_string(self, s):
        return self.decode_bytes(base64.b64decode(s.strip())).decode("utf-8")

    # ── 兼容旧调用: 固定 IV 解密 (用于排查历史假设) ─────────
    def decode_fixed_iv(self, s, iv_hex):
        """用指定固定 IV 解密整段 base64 (不前置 IV)"""
        iv = bytes.fromhex(iv_hex)
        raw = base64.b64decode(s.strip())
        cipher = Cipher(algorithms.AES(self.key), modes.CBC(iv), backend=default_backend())
        d = cipher.decryptor()
        try:
            return self._pkcs7_unpad(d.update(raw) + d.finalize()).decode("utf-8")
        except Exception:
            return None
