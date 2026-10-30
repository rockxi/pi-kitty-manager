ARG PYTHON_IMAGE=python:3.12-alpine
FROM ${PYTHON_IMAGE}
WORKDIR /app
COPY web/ /app/web/
USER 65534:65534
EXPOSE 8500
CMD ["python", "-u", "web/server.py"]
