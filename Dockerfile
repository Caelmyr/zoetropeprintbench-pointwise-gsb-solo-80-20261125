ARG ZOEBENCH_RUNTIME_IMAGE=python:3.12-slim
FROM ${ZOEBENCH_RUNTIME_IMAGE}
USER root
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOST=0.0.0.0 PORT=8000 ZOEBENCH_DATA=/var/lib/zoebench
WORKDIR /opt/zoebench
COPY requirements.txt scripts/check_dependencies.py ./
RUN (python check_dependencies.py || python -m pip install --disable-pip-version-check --no-cache-dir --only-binary=:all: -r requirements.txt) \
    && python check_dependencies.py && python -m pip check
COPY zoebench ./zoebench
COPY image_lab ./image_lab
COPY static ./static
COPY templates ./templates
RUN useradd -U -u 11537 zoebench && install -d -o zoebench -g zoebench /var/lib/zoebench
USER zoebench
EXPOSE 8000
CMD ["python", "-m", "zoebench"]
