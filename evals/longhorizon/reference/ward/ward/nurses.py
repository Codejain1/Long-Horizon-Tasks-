"""Nurses."""

import uuid

from ward.store import NURSES


def add_nurse(name, badge):
    """Register a nurse; returns their id."""
    nid = uuid.uuid4().hex
    NURSES[nid] = {"id": nid, "name": name, "badge": badge}
    return nid
