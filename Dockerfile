FROM registry.access.redhat.com/ubi9/python-312:latest

RUN pip install --no-cache-dir ogx[starter] openai

# should consider eventually switching to Konflux-version-of-ogxai/distribution-starter
# AVOID modifying stuff above this line
# PREFER modifying stuff below this line

COPY ogx_skills_fs/ /opt/app-root/lib/python3.12/site-packages/ogx_skills_fs/
