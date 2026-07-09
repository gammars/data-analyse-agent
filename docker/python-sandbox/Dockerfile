FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV MPLBACKEND=Agg
ENV PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY requirements.txt /tmp/python-sandbox-requirements.txt
RUN pip install --upgrade pip \
    && pip install -r /tmp/python-sandbox-requirements.txt \
    && rm /tmp/python-sandbox-requirements.txt

RUN useradd --create-home --shell /usr/sbin/nologin sandbox
USER sandbox

CMD ["python", "--version"]
