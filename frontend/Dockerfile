# NetZero Hub web front end (static HTML/CSS/JS from github.com/rbellazar/NetZero-Hub, commit fee370e,
# connected to the microservices). Served by nginx; the gateway forwards "/" here.
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY site /usr/share/nginx/html
HEALTHCHECK --interval=10s --timeout=3s --start-period=3s --retries=3 CMD wget -q -O /dev/null http://127.0.0.1/login.html || exit 1
