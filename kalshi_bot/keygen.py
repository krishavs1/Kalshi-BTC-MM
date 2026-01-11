from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

def generate_keys():
    # 1. Generate Private Key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048
    )

    # 2. Save Private Key (KEEP THIS SECRET)
    with open("kalshi-key.pem", "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ))

    # 3. Save Public Key (UPLOAD THIS TO KALSHI)
    public_key = private_key.public_key()
    with open("kalshi-public.pem", "wb") as f:
        f.write(public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ))

    print("✅ Keys generated!")
    print("1. 'kalshi-key.pem' (Your private secret)")
    print("2. 'kalshi-public.pem' (Upload this file content to Kalshi)")

if __name__ == "__main__":
    generate_keys()


