"""minder_web — localhost read-only operator console (8E).

Optional web extra: requires fastapi + uvicorn + jinja2
(requirements-web.txt). Never imported by hook.py, proxy.py, or any
hot runtime path; the CLI (minder_op) has no dependency on this
package.
"""
__version__ = "0.1"
