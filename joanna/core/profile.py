from __future__ import annotations

from datetime import datetime

from joanna.core.features import extract_features
from joanna.core.governance import usable_for_profile
from joanna.core.memory import JoannaMemory
from joanna.core.schema import ExperienceEvent, ExperienceFeature, FeatureKind, ProfileClaim, ProfileType


def refresh_profile_claims(memory: JoannaMemory, events: list[ExperienceEvent]) -> list[ProfileClaim]:
    eligible = [event for event in events if usable_for_profile(event)]
    claims: list[ProfileClaim] = []
    claims.extend(_declared_claims(eligible))
    claims.extend(_feature_pattern_claims(extract_features(eligible, memory=memory)))
    for claim in claims:
        memory.upsert_profile(claim)
    return memory.list_profiles()


def _declared_claims(events: list[ExperienceEvent]) -> list[ProfileClaim]:
    claims: list[ProfileClaim] = []
    for event in events:
        if event.event_type != "preference_statement":
            continue
        claim_text = event.content.get("claim") or event.summary
        claims.append(
            ProfileClaim(
                id=f"profile.declared.{event.id}",
                claim=str(claim_text),
                profile_type=ProfileType.DECLARED,
                evidence=[event.to_evidence()],
                created_at=event.occurred_at,
                updated_at=datetime.now(),
                confidence=min(0.95, event.confidence),
                user_confirmed=True,
            )
        )
    return claims


def _feature_pattern_claims(features: list[ExperienceFeature]) -> list[ProfileClaim]:
    claims: list[ProfileClaim] = []
    all_kinds = tuple(sorted({feature.kind for feature in features if feature.polarity != "preference"}))
    for key in _pattern_keys(all_kinds):
        matched = _dedupe_features(_features_for_key(key, features))
        if len({feature.kind for feature in matched}) < 2 or len({feature.event_id for feature in matched}) < 2:
            continue
        first = min(matched, key=lambda item: item.evidence.occurred_at)
        labels = _labels_for_key(key)
        evidence_by_event = {feature.evidence.event_id: feature.evidence for feature in matched}
        claims.append(
            ProfileClaim(
                id=f"profile.pattern.{'.'.join(key)}",
                claim=f"多条经验中出现了{'、'.join(labels)}的组合；这是待确认模式，不是诊断或事实定论。",
                profile_type=ProfileType.UNCONFIRMED_PATTERN,
                evidence=list(evidence_by_event.values()),
                created_at=datetime.fromisoformat(first.evidence.occurred_at),
                updated_at=datetime.now(),
                confidence=round(min(0.82, 0.42 + len(matched) * 0.04), 2),
                user_confirmed=False,
            )
        )
    return claims


def _pattern_keys(kinds: tuple[str, ...]) -> list[tuple[str, ...]]:
    kind_set = set(kinds)
    keys: list[tuple[str, ...]] = []
    if FeatureKind.SOCIAL_LOAD in kind_set and (
        FeatureKind.BODY_ACTIVATION in kind_set
        or FeatureKind.EXPRESSION_LOAD in kind_set
        or FeatureKind.RECOVERY_DEBT in kind_set
    ):
        keys.append(tuple(sorted(kind for kind in kind_set if kind in {
            FeatureKind.SOCIAL_LOAD,
            FeatureKind.BODY_ACTIVATION,
            FeatureKind.EXPRESSION_LOAD,
            FeatureKind.RECOVERY_DEBT,
            FeatureKind.SELF_DOWNPLAY,
            FeatureKind.TIME_PRESSURE,
        })))
    if FeatureKind.TIME_PRESSURE in kind_set and (
        FeatureKind.FAMILY_PULL in kind_set or FeatureKind.LOCATION_STAY in kind_set
    ):
        keys.append(tuple(sorted(kind for kind in kind_set if kind in {
            FeatureKind.TIME_PRESSURE,
            FeatureKind.FAMILY_PULL,
            FeatureKind.LOCATION_STAY,
        })))
    if FeatureKind.RELATIONSHIP_FRICTION in kind_set and (
        FeatureKind.REFLECTIVE_INTENT in kind_set or FeatureKind.EXPRESSION_LOAD in kind_set
    ):
        keys.append(tuple(sorted(kind for kind in kind_set if kind in {
            FeatureKind.RELATIONSHIP_FRICTION,
            FeatureKind.REFLECTIVE_INTENT,
            FeatureKind.EXPRESSION_LOAD,
            FeatureKind.SELF_DOWNPLAY,
        })))
    return keys


def _features_for_key(key: tuple[str, ...], features: list[ExperienceFeature]) -> list[ExperienceFeature]:
    selected = [
        feature
        for feature in features
        if feature.kind in key and feature.polarity != "preference"
    ]
    key_set = set(key)
    if {
        FeatureKind.TIME_PRESSURE,
        FeatureKind.FAMILY_PULL,
        FeatureKind.LOCATION_STAY,
    }.issubset(key_set):
        anchor_dates = {
            feature.evidence.occurred_at[:10]
            for feature in selected
            if feature.kind in {FeatureKind.FAMILY_PULL, FeatureKind.LOCATION_STAY}
        }
        selected = [
            feature
            for feature in selected
            if feature.kind != FeatureKind.TIME_PRESSURE
            or feature.evidence.occurred_at[:10] in anchor_dates
        ]
    return selected


def _labels_for_key(key: tuple[str, ...]) -> list[str]:
    labels = {
        FeatureKind.BODY_ACTIVATION: "身体激活",
        FeatureKind.RECOVERY_DEBT: "恢复不足",
        FeatureKind.SOCIAL_LOAD: "互动负荷",
        FeatureKind.TIME_PRESSURE: "时间压力",
        FeatureKind.RELATIONSHIP_FRICTION: "关系摩擦",
        FeatureKind.FAMILY_PULL: "家庭牵引",
        FeatureKind.EXPRESSION_LOAD: "表达负荷",
        FeatureKind.LOCATION_STAY: "地点滞留",
        FeatureKind.SELF_DOWNPLAY: "用户弱化",
        FeatureKind.REFLECTIVE_INTENT: "复盘意图",
    }
    return [labels.get(kind, kind) for kind in key]


def _dedupe_features(features: list[ExperienceFeature]) -> list[ExperienceFeature]:
    by_id: dict[str, ExperienceFeature] = {}
    for feature in features:
        by_id[feature.id] = feature
    return sorted(by_id.values(), key=lambda item: (item.evidence.occurred_at, item.kind))
