FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates curl gnupg tini vim \
    && install -d /etc/apt/keyrings \
    && curl -fsSL https://repo.powerdns.com/FD380FBB-pub.asc \
       | tee /etc/apt/keyrings/auth-50-pub.asc >/dev/null \
    && echo "deb [signed-by=/etc/apt/keyrings/auth-50-pub.asc] http://repo.powerdns.com/debian bookworm-auth-50 main" \
       > /etc/apt/sources.list.d/pdns.list \
    && printf "Package: pdns-*\nPin: origin repo.powerdns.com\nPin-Priority: 600\n" \
       > /etc/apt/preferences.d/auth-50 \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       pdns-server pdns-backend-mysql \
    && apt-get purge -y --auto-remove curl gnupg \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 53/udp 53/tcp 8081/tcp

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
