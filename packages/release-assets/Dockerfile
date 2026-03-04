FROM alpine:3.20

WORKDIR /assets
COPY release/final/ /assets/

CMD ["sh", "-lc", "echo release assets in /assets; ls -lah /assets"]
