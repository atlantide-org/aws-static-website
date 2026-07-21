"""Atlantide component providing an S3 + CloudFront static website.

Installed with ``atlantide component add <repo-url> --ref v1 --subdir src`` and
imported as ``atlantide.components.aws_static_website``. See README.md.
"""

from __future__ import annotations

from atlantide.core import Component, child, current_stack_region
from atlantide.core.errors import RegistryError
from atlantide.providers.aws import (
    CloudFrontDistribution,
    OriginAccessControl,
    S3Bucket,
    S3BucketPolicy,
    ServicePrincipal,
    allow,
)

__all__ = ["StaticWebsite"]


class StaticWebsite(Component):
    """S3 + CloudFront static website served over the default CloudFront domain.

    Expands to four resources: a private origin ``S3Bucket``, an
    ``OriginAccessControl``, a ``CloudFrontDistribution`` fronting the bucket, and an
    ``S3BucketPolicy`` granting ``s3:GetObject`` to that distribution only, scoped by
    ``AWS:SourceArn``. References order them as origin + oac -> cdn -> policy.

    Exposes ``origin``, ``oac``, ``cdn``, ``policy``, ``bucket``, ``url``, ``arn``,
    and ``distribution_id``.
    """

    def __init__(
        self,
        name: str,
        *,
        bucket: str,
        region: str | None = None,
        default_root_object: str = "index.html",
        comment: str = "",
        versioning: bool = False,
    ) -> None:
        resolved = region or current_stack_region()
        if resolved is None:
            raise RegistryError("StaticWebsite needs a region (pass region= or use a Stack)")

        self.origin = child(
            S3Bucket, "origin", bucket=bucket, region=resolved, versioning=versioning
        )
        self.oac = child(
            OriginAccessControl,
            "oac",
            oac_name=f"{name}-oac",
            description=f"OAC for {name}",
        )
        self.cdn = child(
            CloudFrontDistribution,
            "cdn",
            origin_domain=self.origin.regional_domain_name,
            oac_id=self.oac.oac_id,
            default_root_object=default_root_object,
            comment=comment,
        )
        self.policy = child(
            S3BucketPolicy,
            "policy",
            bucket=self.origin.bucket,
            statements=[
                allow(
                    S3Bucket.Action.GetObject,
                    on=self.origin.objects_arn,
                    principal={"Service": ServicePrincipal.CloudFront},
                    condition={"StringEquals": {"AWS:SourceArn": self.cdn.arn}},
                )
            ],
        )

        self.bucket = self.origin.bucket
        self.arn = self.cdn.arn
        self.distribution_id = self.cdn.distribution_id
        self.url = self.cdn.domain_name
