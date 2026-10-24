"""Resolves what an organization actually pays for platform access.

The built-in SUBSCRIPTION_PRICING / ONE_TIME_PRICING tables are only the
default. The System Maintainer can override them with SubscriptionPriceRule
rows (System Maintenance -> Subscription pricing), for one hospital or for
all hospitals, at one level or at every level. Nothing about any specific
facility lives in code.

Specificity decides which rule wins (most specific first):
  1. rule for a specific hospital of the organization
  2. rule for all hospitals at the organization's level
  3. rule for all hospitals at every level
  4. the default table
Within the same tier, the most recently updated active rule wins.
"""
from app.models import (
    SubscriptionPriceRule, Hospital, SUBSCRIPTION_PRICING, ONE_TIME_PRICING,
)


def default_price(level, plan_type):
    table = ONE_TIME_PRICING if plan_type == "purchase" else SUBSCRIPTION_PRICING
    return table.get(level, 0)


def find_rule(org, plan_type):
    """The active SubscriptionPriceRule that applies to this organization
    for this plan type, or None when the default price applies."""
    if org is None:
        return None
    hospital_ids = {h.id for h in Hospital.query.filter_by(organization_id=org.id).all()}
    rules = SubscriptionPriceRule.query.filter_by(plan_type=plan_type, is_active=True).order_by(
        SubscriptionPriceRule.updated_at.desc(), SubscriptionPriceRule.id.desc()
    ).all()

    def tier(rule):
        """0 = specific hospital, 1 = a level, 2 = everyone; None = not for this org."""
        level_ok = rule.level is None or rule.level == org.plan_level
        if rule.hospital_id is not None:
            return 0 if rule.hospital_id in hospital_ids and level_ok else None
        if rule.level is not None:
            return 1 if level_ok else None
        return 2

    best, best_tier = None, 99
    for rule in rules:  # newest first, so the first hit in a tier wins ties
        t = tier(rule)
        if t is not None and t < best_tier:
            best, best_tier = rule, t
    return best


def resolve_price(org, plan_type):
    """-> (amount in KES as a number, the rule applied or None)."""
    rule = find_rule(org, plan_type)
    if rule is not None:
        return float(rule.amount), rule
    return default_price(org.plan_level if org else None, plan_type), None
