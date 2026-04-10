FROM python:3.11-slim

# Install system dependencies for matplotlib and OpenSees
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ gfortran \
    liblapack-dev libblas-dev \
    tcl8.6 tcl8.6-dev \
    wget curl \
    && rm -rf /var/lib/apt/lists/*

# Install OpenSeesPy and opensees console binary
RUN pip install --no-cache-dir openseespy==3.8.0.1 || true

# Download OpenSees 3.8.0 Linux binary
RUN mkdir -p /opt/opensees && \
    wget -q "https://github.com/OpenSees/OpenSees/releases/download/v3.8.0/OpenSees-3.8.0-Linux.tar.gz" \
    -O /tmp/opensees.tar.gz && \
    tar -xzf /tmp/opensees.tar.gz -C /opt/opensees --strip-components=1 && \
    rm /tmp/opensees.tar.gz && \
    chmod +x /opt/opensees/bin/OpenSees || \
    echo "Binary download failed, will rely on openseespy or PATH"

ENV PATH="/opt/opensees/bin:${PATH}"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data directories
RUN mkdir -p data/uploads data/runs

EXPOSE 10000

CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:10000", "--workers", "2", "--timeout", "600", "--threads", "4"]
