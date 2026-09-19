from pathlib import Path


DEPLOYER = Path("deploy/turbot-deploy.sh")
WORKFLOW = Path(".github/workflows/deploy-bundle.yml")


def test_ai_lead_assist_has_explicit_reversible_production_config_marker():
    text = DEPLOYER.read_text(encoding="utf-8")

    assert 'TURBOT_AI_LEAD_ASSIST_CONFIG_V1' in text
    assert 'AI_LEAD_ASSIST_ENABLED' in text
    assert 'desired" != "true"' in text
    assert 'desired" != "false"' in text
    assert 'lead_assist_enabled' in text
    assert 'ai.get("ready")' in text


def test_bundle_installs_new_deployer_before_enabling_ai_lead_assist():
    text = WORKFLOW.read_text(encoding="utf-8")

    bundle_pos = text.index("TURBOT_DEPLOY_BUNDLE_V1")
    assist_pos = text.index("TURBOT_AI_LEAD_ASSIST_CONFIG_V1")

    assert bundle_pos < assist_pos
    assert 'AI_LEAD_ASSIST_ENABLED: "true"' in text
    assert 'ai.get("lead_assist_enabled") is not True' in text
    assert 'ai.get("ready") is not True' in text
