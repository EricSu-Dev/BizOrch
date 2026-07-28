"""Static Nginx deployment boundaries."""

from pathlib import Path


NGINX_CONFIG = Path(__file__).resolve().parents[3] / "deploy" / "nginx" / "bizorch.conf"


def test_versioned_frontend_root_keeps_acme_challenges_outside_current_release() -> None:
    content = NGINX_CONFIG.read_text(encoding="utf-8")

    assert "root /var/www/bizorch/current;" in content
    assert "location ^~ /.well-known/acme-challenge/" in content
    assert "root /var/www/bizorch;" in content
    assert "try_files $uri =404;" in content
