"""
s3_uploader.py — S3 업로드 공통 모듈
======================================
- UPLOAD_S3=true 일 때만 동작
- boto3 미설치/자격증명 누락/네트워크 실패 시 경고만 남기고 None 반환
- 호출자는 None일 때 외부 URL 또는 로컬 path를 fallback으로 사용

환경변수:
  UPLOAD_S3            "true"/"false"
  S3_BUCKET            버킷명
  S3_PREFIX            (선택) 키 prefix, default "review_images"
  AWS_REGION           (또는 AWS_DEFAULT_REGION)
  AWS_ACCESS_KEY_ID    boto3가 자동으로 읽음
  AWS_SECRET_ACCESS_KEY boto3가 자동으로 읽음

⚠️ 절대 .env 값을 print/log에 노출하지 말 것. 이 모듈도 키 값을 출력하지 않는다.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import threading
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger("s3_uploader")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[s3_uploader] %(levelname)s: %(message)s"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)

_client_lock = threading.Lock()
_client = None
_disabled_reason: Optional[str] = None


def is_enabled() -> bool:
    return str(os.environ.get("UPLOAD_S3", "")).strip().lower() == "true"


def _bucket() -> str:
    return os.environ.get("S3_BUCKET", "").strip()


def _prefix() -> str:
    p = os.environ.get("S3_PREFIX", "review_images").strip().strip("/")
    return p


def _region() -> str:
    return (os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or "ap-northeast-2").strip()


def _get_client():
    """싱글톤 클라이언트. 실패 시 None 반환 + 1회 경고."""
    global _client, _disabled_reason
    if _client is not None or _disabled_reason is not None:
        return _client

    with _client_lock:
        if _client is not None or _disabled_reason is not None:
            return _client

        if not is_enabled():
            _disabled_reason = "UPLOAD_S3 != true"
            return None
        if not _bucket():
            _disabled_reason = "S3_BUCKET 미설정"
            logger.warning("S3 비활성화: S3_BUCKET 환경변수가 비어 있음")
            return None

        try:
            import boto3
            from botocore.config import Config
        except ImportError:
            _disabled_reason = "boto3 미설치"
            logger.warning("S3 비활성화: boto3 미설치 (pip install boto3)")
            return None

        try:
            _client = boto3.client(
                "s3",
                region_name=_region(),
                config=Config(retries={"max_attempts": 2, "mode": "standard"},
                              connect_timeout=5, read_timeout=15),
            )
            return _client
        except Exception as e:
            _disabled_reason = f"client init 실패: {type(e).__name__}"
            logger.warning(f"S3 비활성화: 클라이언트 초기화 실패 ({type(e).__name__})")
            return None


def _public_url(key: str) -> str:
    return f"https://{_bucket()}.s3.{_region()}.amazonaws.com/{key}"


def upload_file(local_path: str, key: Optional[str] = None,
                content_type: Optional[str] = None) -> Optional[str]:
    """
    로컬 파일을 S3에 업로드하고 public URL 반환.
    실패 시 None.
    """
    if not is_enabled():
        return None
    if not local_path or not os.path.exists(local_path):
        logger.warning(f"업로드 스킵: 파일 없음 — {local_path}")
        return None

    cli = _get_client()
    if cli is None:
        return None

    if key is None:
        fname = os.path.basename(local_path)
        key = f"{_prefix()}/{fname}" if _prefix() else fname

    if content_type is None:
        ct, _ = mimetypes.guess_type(local_path)
        content_type = ct or "application/octet-stream"

    try:
        cli.upload_file(
            Filename=local_path,
            Bucket=_bucket(),
            Key=key,
            ExtraArgs={"ContentType": content_type},
        )
        return _public_url(key)
    except Exception as e:
        # 키 값/시크릿은 출력하지 않음. 예외 타입만 로깅.
        logger.warning(f"S3 업로드 실패 [{key}]: {type(e).__name__}")
        return None


def upload_if_enabled(local_path: str, key: Optional[str] = None) -> Optional[str]:
    """
    UPLOAD_S3 비활성/실패 시 조용히 None.
    호출자가 None일 때 fallback URL을 쓰면 된다.
    """
    try:
        return upload_file(local_path, key=key)
    except Exception as e:
        logger.warning(f"upload_if_enabled 예외 — {type(e).__name__}")
        return None


def status() -> dict:
    """진단용. 비밀값은 노출하지 않는다."""
    return {
        "enabled":          is_enabled(),
        "bucket_set":       bool(_bucket()),
        "prefix":           _prefix(),
        "region":           _region(),
        "boto3_available":  _try_import_boto3(),
        "disabled_reason":  _disabled_reason,
    }


def _try_import_boto3() -> bool:
    try:
        import boto3  # noqa: F401
        return True
    except ImportError:
        return False


if __name__ == "__main__":
    # 진단 출력 (비밀값 없음)
    import json
    print(json.dumps(status(), ensure_ascii=False, indent=2))
