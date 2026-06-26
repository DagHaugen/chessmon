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
CERT = os.path.join(ROOT, "cert.pem")            # fullchain (leaf + CA) the server presents
KEY = os.path.join(ROOT, "key.pem")              # the leaf's private key
CACERT = os.path.join(ROOT, "chessmon-ca.pem")      # the root CA cert -> install + trust THIS once on each device
CAKEY = os.path.join(ROOT, "chessmon-ca-key.pem")   # the root's private key, kept so the CA is REUSED (trusted devices stay trusted); secret


def _private(ip):
    if ip.startswith("192.168.") or ip.startswith("10."):
        return True
    if ip.startswith("172."):
        try:
            return 16 <= int(ip.split(".")[1]) <= 31
        except (IndexError, ValueError):
            return False
    return False


def lan_ips():
    """All private IPv4 addresses, Wi-Fi/Ethernet (192.168.* / 10.*) first. The default route
    often lands on a virtual adapter (Hyper-V/WSL, usually 172.*) the phone can't reach, so we
    enumerate and prefer the real LAN one instead of trusting a single connect() trick."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))            # no packets sent; reveals the default-route IP
        ips.add(s.getsockname()[0])
    except Exception:
        pass
    finally:
        s.close()
    cand = sorted((ip for ip in ips if _private(ip)),
                  key=lambda ip: (0 if ip.startswith(("192.168.", "10.")) else 1, ip))
    return cand or ["127.0.0.1"]


def _load_or_make_ca(nvb, nva):
    """The root CA, created ONCE and then reused — so a device that trusts it stays trusted across IP changes
    and leaf reissues. Returns (cert, private_key)."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    if os.path.exists(CACERT) and os.path.exists(CAKEY):
        ca = x509.load_pem_x509_certificate(open(CACERT, "rb").read())
        ca_key = serialization.load_pem_private_key(open(CAKEY, "rb").read(), password=None)
        return ca, ca_key
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "chessmon root")])
    ca = (x509.CertificateBuilder()
          .subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(nvb).not_valid_after(nva)
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(key_cert_sign=True, crl_sign=True, digital_signature=True,
                                       content_commitment=False, key_encipherment=False, data_encipherment=False,
                                       key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    with open(CAKEY, "wb") as f:
        f.write(ca_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                     serialization.NoEncryption()))
    with open(CACERT, "wb") as f:
        f.write(ca.public_bytes(serialization.Encoding.PEM))
    return ca, ca_key


def make_cert(ips):
    """Reissue the LEAF server cert (CA:false + serverAuth), signed by the stable root CA. The CA is reused, so a
    device that trusts it once keeps working across IP changes and reissues; only the leaf + fullchain change."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    now = datetime.datetime.now(datetime.timezone.utc)
    nvb = now - datetime.timedelta(days=1)
    nva = now + datetime.timedelta(days=824)                      # <=825-day span — iOS 13+/macOS reject longer
    ca, ca_key = _load_or_make_ca(nvb, nva)

    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    san = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    for ip in ips:
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            pass
    leaf = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "chessmon")]))
            .issuer_name(ca.subject).public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(nvb).not_valid_after(nva)
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=True, key_cert_sign=False,
                                         crl_sign=False, content_commitment=False, data_encipherment=False,
                                         key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))                       # signed by the ROOT key

    with open(KEY, "wb") as f:
        f.write(leaf_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                       serialization.NoEncryption()))
    with open(CERT, "wb") as f:                                   # leaf first, then the CA -> the server sends the chain
        f.write(leaf.public_bytes(serialization.Encoding.PEM) + ca.public_bytes(serialization.Encoding.PEM))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "gencert":   # make/refresh the cert and exit (used by setup.ps1) — no server
        try:
            make_cert(lan_ips())
        except ImportError:
            print("need the cryptography package:  .venv\\Scripts\\pip install cryptography")
            return 1
        print(f"generated self-signed cert for: localhost, 127.0.0.1, {', '.join(lan_ips())}")
        return 0
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    force = len(sys.argv) > 2 and sys.argv[2] == "new"
    ips = lan_ips()
    if force or not (os.path.exists(CERT) and os.path.exists(KEY)):
        try:
            make_cert(ips)
        except ImportError:
            print("need the cryptography package:  .venv\\Scripts\\pip install cryptography")
            return 1
        print(f"generated self-signed cert for: localhost, 127.0.0.1, {', '.join(ips)}")
    print(f"\n  clock  : https://{ips[0]}:{port}/   <- open this on the clock phone")
    if len(ips) > 1:
        print("  if it times out, try:        "
              + ", ".join(f"https://{a}:{port}/" for a in ips[1:]))
    print("  camera : opens automatically from the clock's QR")
    print(f"  still timing out on every address? the Windows Firewall is blocking port {port}")
    print("           - see server/README.md to allow it (one admin command)\n")
    sys.path.insert(0, ROOT)
    import uvicorn
    uvicorn.run("server.app:app", host="0.0.0.0", port=port,
                ssl_keyfile=KEY, ssl_certfile=CERT,
                timeout_graceful_shutdown=3)        # don't hang on open WebSockets — force-close 3s after Ctrl+C
    return 0


if __name__ == "__main__":
    sys.exit(main())
