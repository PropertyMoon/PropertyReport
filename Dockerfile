# PropertyReport runtime image.
#
# Built explicitly via Dockerfile (instead of nixpacks) so we have full control
# over the system libraries WeasyPrint dlopen()s at render time. nixpacks's
# aptPkgs were silently failing to install glib/pango into a path the loader
# could find, producing:
#   "cannot load library 'libgobject-2.0-0' ..."
# Switching to a plain Debian base + apt-get fixes this for good.

FROM python:3.11-slim-bookworm

# WeasyPrint runtime deps. glib (libgobject), pango, cairo, harfbuzz are the
# minimum; gdk-pixbuf is needed for raster image embedding; shared-mime-info
# and fontconfig are needed for image type detection and font discovery; the
# two font packages give us a sane default if Inter from Google Fonts is
# unreachable at render time.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libcairo2 \
        libglib2.0-0 \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
        shared-mime-info \
        fontconfig \
        fonts-dejavu-core \
        fonts-liberation \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so we get good Docker layer caching when the
# application source changes but the deps don't.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# Build-time verification: if WeasyPrint can't load its system libs from
# this image, fail the build NOW rather than shipping a broken container.
# This forces us to fix Dockerfile deps instead of falling back at runtime.
RUN python -c "from weasyprint import HTML; HTML(string='<p>ok</p>').write_pdf('/tmp/_check.pdf'); print('WeasyPrint import + render OK')"

COPY . .

ENV PYTHONUNBUFFERED=1 \
    PORT=8000

# Railway injects $PORT at runtime. Shell-form so it expands.
CMD uvicorn api:app --host 0.0.0.0 --port ${PORT} --workers 2
