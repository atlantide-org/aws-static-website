from __future__ import annotations

import pytest

from atlantide.core import Ref, RegistryError, Stack, collecting
from atlantide.graph import build_graph
from atlantide.ir import lower
from atlantide.providers.aws import Region, S3Bucket, ServicePrincipal

from src import StaticWebsite


# --- Expansion and child namespacing --------------------------------------


def test_expands_to_four_namespaced_children() -> None:
    with Stack("site", region=Region.UsEast1, name_prefix="acme", tags={"app": "www"}):
        site = StaticWebsite("web", bucket="acme-www-abcd1234", comment="test")

        assert site.origin.node_id.endswith("aws.S3Bucket:web-origin")
        assert site.oac.node_id.endswith("aws.OriginAccessControl:web-oac")
        assert site.cdn.node_id.endswith("aws.CloudFrontDistribution:web-cdn")
        assert site.policy.node_id.endswith("aws.S3BucketPolicy:web-policy")


def test_two_instances_do_not_collide() -> None:
    with Stack("site", region=Region.UsEast1, name_prefix="acme"):
        a = StaticWebsite("blog", bucket="acme-blog")
        b = StaticWebsite("docs", bucket="acme-docs")

        ids = {
            a.origin.node_id,
            a.cdn.node_id,
            b.origin.node_id,
            b.cdn.node_id,
        }
        assert len(ids) == 4
        assert "blog-origin" in a.origin.node_id
        assert "docs-origin" in b.origin.node_id


# --- Region resolution ----------------------------------------------------


def test_requires_region_without_stack_or_param() -> None:
    with pytest.raises(RegistryError):
        StaticWebsite("web", bucket="b")


def test_falls_back_to_stack_region() -> None:
    with Stack("site", region=Region.EuNorth1, name_prefix="acme"):
        site = StaticWebsite("web", bucket="acme-www")
        assert site.origin.region == Region.EuNorth1


def test_region_param_overrides_stack_region() -> None:
    with Stack("site", region=Region.UsEast1, name_prefix="acme"):
        site = StaticWebsite("web", bucket="acme-www", region=Region.EuWest1)
        assert site.origin.region == Region.EuWest1


# --- Parameter propagation to children ------------------------------------


def test_defaults() -> None:
    with Stack("site", region=Region.UsEast1, name_prefix="acme"):
        site = StaticWebsite("web", bucket="acme-www")
        assert site.origin.versioning is False
        assert site.cdn.default_root_object == "index.html"
        assert site.cdn.comment == ""


def test_versioning_propagates_to_origin() -> None:
    with Stack("site", region=Region.UsEast1, name_prefix="acme"):
        site = StaticWebsite("web", bucket="acme-www", versioning=True)
        assert site.origin.versioning is True


def test_root_object_and_comment_propagate_to_cdn() -> None:
    with Stack("site", region=Region.UsEast1, name_prefix="acme"):
        site = StaticWebsite(
            "web", bucket="acme-www", default_root_object="main.html", comment="prod site"
        )
        assert site.cdn.default_root_object == "main.html"
        assert site.cdn.comment == "prod site"


def test_oac_name_and_description_derived_from_name() -> None:
    with Stack("site", region=Region.UsEast1, name_prefix="acme"):
        site = StaticWebsite("web", bucket="acme-www")
        assert site.oac.oac_name == "web-oac"
        assert site.oac.description == "OAC for web"


# --- Reference wiring and exposed outputs ---------------------------------


def test_cdn_wires_origin_and_oac_refs() -> None:
    with Stack("site", region=Region.UsEast1, name_prefix="acme"):
        site = StaticWebsite("web", bucket="acme-www")

        origin_domain = site.cdn.origin_domain
        assert isinstance(origin_domain, Ref)
        assert origin_domain.node_id == site.origin.node_id
        assert origin_domain.attr == "regional_domain_name"

        oac_id = site.cdn.oac_id
        assert isinstance(oac_id, Ref)
        assert oac_id.node_id == site.oac.node_id
        assert oac_id.attr == "oac_id"


def test_exposed_outputs_map_to_children() -> None:
    with Stack("site", region=Region.UsEast1, name_prefix="acme"):
        site = StaticWebsite("web", bucket="acme-www")

        assert site.bucket == site.origin.bucket == "acme-www"

        for value, attr in (
            (site.url, "domain_name"),
            (site.arn, "arn"),
            (site.distribution_id, "distribution_id"),
        ):
            assert isinstance(value, Ref)
            assert value.node_id == site.cdn.node_id
            assert value.attr == attr


def test_policy_grants_getobject_to_distribution_only() -> None:
    with Stack("site", region=Region.UsEast1, name_prefix="acme"):
        site = StaticWebsite("web", bucket="acme-www")

        stmt = site.policy.statements[0]
        assert stmt["Effect"] == "Allow"
        assert list(stmt["Action"]) == [S3Bucket.Action.GetObject]

        resource = stmt["Resource"]
        assert isinstance(resource, Ref)
        assert resource.node_id == site.origin.node_id
        assert resource.attr == "objects_arn"

        assert stmt["Principal"] == {"Service": ServicePrincipal.CloudFront}

        source_arn = stmt["Condition"]["StringEquals"]["AWS:SourceArn"]
        assert isinstance(source_arn, Ref)
        assert source_arn.node_id == site.cdn.node_id
        assert source_arn.attr == "arn"


# --- Dependency ordering in the lowered IR and graph -----------------------


def test_dependency_edges() -> None:
    with collecting() as reg, Stack("site", region=Region.UsEast1, name_prefix="acme"):
        site = StaticWebsite("web", bucket="acme-www")

    ir = lower(reg)
    cdn = ir.node(site.cdn.node_id)
    assert cdn is not None
    assert set(cdn.dependencies) == {site.origin.node_id, site.oac.node_id}

    policy = ir.node(site.policy.node_id)
    assert policy is not None
    assert site.cdn.node_id in policy.dependencies

    graph = build_graph(ir).unwrap()
    assert set(graph.deps[site.cdn.node_id]) == {site.origin.node_id, site.oac.node_id}
    assert site.cdn.node_id in graph.deps[site.policy.node_id]
