"""Runtime configuration, overridable through environment variables."""
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/geo.db")

# Hard limits protecting the service from oversized / malicious uploads.
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "50")) * 1024 * 1024
MAX_UNZIPPED_BYTES = int(os.getenv("MAX_UNZIPPED_MB", "300")) * 1024 * 1024
MAX_ZIP_MEMBERS = int(os.getenv("MAX_ZIP_MEMBERS", "500"))

ALLOWED_EXTENSIONS = {".zip", ".kml"}
