FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY openflight_dashboard ./openflight_dashboard
RUN pip install --no-cache-dir .

ENV OPENFLIGHT_DASHBOARD_DATA=/data
VOLUME /data
EXPOSE 8090
CMD ["openflight-dashboard"]
