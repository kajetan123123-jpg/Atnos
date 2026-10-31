"""Pending archive recovery is never permission to publish a blocked order."""
import pytest
from atnos.production import publication_coordinator as pc
from atnos.production.project_profile import content_hash
from test_publication_coordinator import ready,store

def test_only_exact_failed_local_registration_can_reuse_intact_package(store):
    job=ready(store,delivery_mode='local_only')
    inputs={'fixture':'registered archive operation'}; digest=content_hash(inputs)
    stage='remotion_refresh/'+digest[:12]
    with store.lease(job) as token:
        store.begin(job,token,stage,inputs)
        store.fail(job,token,stage,category='dependency_failure',message='Fixture path too long')
    with pytest.raises(pc.PublicationBlocked):pc.verified_package(store,job)
    with pytest.raises(pc.PublicationBlocked):pc.verified_package(store,job,pending_renderer_registration=(stage,'0'*64))
    package=pc.verified_package(store,job,pending_renderer_registration=(stage,digest))
    assert package['state']['status']=='blocked'
    package['files']['video'].write_bytes(b'changed actual file')
    with pytest.raises(pc.PublicationBlocked):pc.verified_package(store,job,pending_renderer_registration=(stage,digest))

def test_pending_recovery_does_not_admit_nonlocal_orders(store):
    job=ready(store)
    inputs={'fixture':'archive'}; digest=content_hash(inputs); stage='remotion_refresh/'+digest[:12]
    with store.lease(job) as token:
        store.begin(job,token,stage,inputs)
        store.fail(job,token,stage,category='dependency_failure',message='Fixture archive failure')
    with pytest.raises(pc.PublicationBlocked):pc.verified_package(store,job,pending_renderer_registration=(stage,digest))
