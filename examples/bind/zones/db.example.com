$TTL 3600
$ORIGIN example.com.

; SOA Record
@       IN      SOA     ns1.example.com. admin.example.com. (
                        1                       ; Serial
                        3600            ; refresh (1 hour)
                        600             ; retry (10 minutes)
                        604800          ; expire (1 week)
                        300             ; minimum TTL (5 minutes)
                        )

; Name Servers
@       IN      NS      ns1.example.com.
@       IN      NS      ns2.example.com.

; Name Server A Records
ns1     IN      A       192.0.2.1
ns2     IN      A       192.0.2.2

; Mail Servers
@       IN      MX      10 mail1.example.com.
@       IN      MX      20 mail2.example.com.
mail1   IN      A       192.0.2.10
mail2   IN      A       192.0.2.11

; Web Servers
www     IN      A       192.0.2.100
www     IN      AAAA    2001:db8::100
@       IN      A       192.0.2.100

; Application Servers
api     IN      A       192.0.2.50
api     IN      AAAA    2001:db8::50
app     IN      CNAME   api.example.com.

; CDN / Static Assets
cdn     IN      CNAME   cdn.cloudprovider.net.
static  IN      CNAME   cdn.example.com.
assets  IN      CNAME   cdn.example.com.

; Database Servers
db      IN      A       192.0.2.30
db-read IN      A       192.0.2.31
db-read IN      A       192.0.2.32

; Cache Servers
cache   IN      A       192.0.2.40
redis   IN      CNAME   cache.example.com.

; TXT Records
@       IN      TXT     "v=spf1 mx ip4:192.0.2.0/24 -all"
_dmarc  IN      TXT     "v=DMARC1; p=reject; rua=mailto:dmarc@example.com"

; Service Records (SRV)
_http._tcp      IN      SRV     10 50 80 www.example.com.
_https._tcp     IN      SRV     10 50 443 www.example.com.
_ldap._tcp      IN      SRV     10 50 389 ldap.example.com.

; LDAP Server
ldap    IN      A       192.0.2.60

; IPv6 Hosts (2001:db8::/32 documentation prefix)
ipv6-host1      IN      AAAA    2001:db8::1
ipv6-host2      IN      AAAA    2001:db8::2
ipv6-host3      IN      AAAA    2001:db8::3

; CAA Records (Certificate Authority Authorization)
@       IN      CAA     0 issue "letsencrypt.org"
@       IN      CAA     0 issuewild "letsencrypt.org"
@       IN      CAA     0 iodef "mailto:security@example.com"

