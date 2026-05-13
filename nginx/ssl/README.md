# ──────────────────────────────────────────────────────────────
# SSL Certificate Placeholder
#
# Place your SSL certificates here:
#   • cert.pem   — fullchain certificate
#   • key.pem    — private key
#
# For Let's Encrypt (free):
#   certbot certonly --standalone -d yourdomain.com
#   cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem ./cert.pem
#   cp /etc/letsencrypt/live/yourdomain.com/privkey.pem   ./key.pem
#
# For self-signed (testing only):
#   openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
#     -keyout key.pem -out cert.pem \
#     -subj "/CN=yourdomain.com"
# ──────────────────────────────────────────────────────────────
