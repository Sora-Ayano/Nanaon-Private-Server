import ipaddress
import socket


def validate_ip(value):
    address = ipaddress.IPv4Address(value)
    if not any(address in ipaddress.IPv4Network(block) for block in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')):
        raise ValueError('请选择同一局域网的私有 IPv4 地址，不可使用回环、组播或链路本地地址。')
    return str(address)


def detect_ip():
    # UDP connect selects an existing route without sending a packet.
    candidates = []
    for route in ('192.0.2.1', '192.168.1.1', '10.0.0.1'):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((route, 9))
                candidates.append(sock.getsockname()[0])
        except OSError:
            pass
    try:
        candidates.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    for value in dict.fromkeys(candidates):
        try:
            return validate_ip(value)
        except ValueError:
            pass
    raise RuntimeError('无法检测局域网 IP，请连接 Wi-Fi/网线或使用 --ip 指定。')
