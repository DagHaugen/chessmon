"""Serve the chessmon server over HTTPS in one command — needed for the camera PWA, since
browsers only allow getUserMedia on a secure origin. Generates a self-signed cert covering
localhost + this machine's LAN IP on first run (no openssl required), then starts uvicorn
with TLS. The phones accept the self-signed warning once.

    python server/serve_https.py            # port 8000
    python server/serve_https.py 8443       # custom port
    python server/serve_https.py 8000 new   # regenerate the cert
"""
import datetime
import ipaddress
import os
import socket
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CERT = os.path.join(ROOT, "cert.pem")
KEY = os.path.join(ROOT, "key.pem")


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))            # no packets sent; just reveals the local IP
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def make_cert(ip):
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "chessmon")])
    san = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    try:
        san.append(x509.IPAddress(ipaddress.ip_address(ip)))
    except ValueError:
        pass
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .sign(key, hashes.SHA256()))
    with open(KEY, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.TraditionalOpenSSL,
                                  serialization.NoEncryption()))
    with open(CERT, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    force = len(sys.argv) > 2 and sys.argv[2] == "new"
    ip = lan_ip()
    if force or not (os.path.exists(CERT) and os.path.exists(KEY)):
        try:
            make_cert(ip)
        except ImportError:
            print("need the cryptography package:  .venv\\Scripts\\pip install cryptography")
            return 1
        print(f"generated self-signed cert (localhost, 127.0.0.1, {ip})")
    print(f"\n  clock  : https://{ip}:{port}/")
    print("  camera : opens from the clock's QR (accept the self-signed warning once)\n")
    sys.path.insert(0, ROOT)
    import uvicorn
    uvicorn.run("server.app:app", host="0.0.0.0", port=port,
                ssl_keyfile=KEY, ssl_certfile=CERT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
