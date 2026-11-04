FROM erikvl87/languagetool:6.6@sha256:8402e6059e705f70c419010dfa9e4348ce25fc5ac49ba5c68834504fb6a782a3 AS distribution

FROM eclipse-temurin:17-jre-jammy@sha256:b3054a20e1a851574cdb1692dd0db5310c647e62214a2a7abecbec1b9e3ae8f1
WORKDIR /opt/LanguageTool
COPY --from=distribution /LanguageTool/ ./
COPY deploy/languagetool.properties /opt/server.properties
USER 10001:10001
# Never forward LanguageTool output, which can contain rejected input, to logs.
ENTRYPOINT ["sh", "-c", "exec java -Xms128m -Xmx512m -cp languagetool-server.jar org.languagetool.server.HTTPServer --config /opt/server.properties --port 8081 --public > /dev/null 2>&1"]
