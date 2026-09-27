"""Inspect the new production slice for forbidden transport/persistence escape."""
import ast
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]/'src'/'clash_rush_rebuild'
FILES=tuple(ROOT.glob('mvp_deployment*.py'))


def test_new_runtime_has_no_image_writer_network_or_adb_import():
    assert len(FILES)==7
    forbidden_modules={'requests','urllib','pytesseract','easyocr','adbutils','pyminitouch','pyautogui'}
    forbidden_calls={'imwrite','save_frame','screenshot','system','popen','Popen','keybd_event'}
    for file in FILES:
        tree=ast.parse(file.read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                assert not {x.name.split('.')[0] for x in node.names}&forbidden_modules
            if isinstance(node,ast.ImportFrom):assert (node.module or '').split('.')[0] not in forbidden_modules
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
                assert node.func.attr not in forbidden_calls,(file.name,node.func.attr)


def test_public_result_has_only_closed_facts_no_frame_or_reference_fields():
    from clash_rush_rebuild.mvp_deployment import DeploymentResult
    result=DeploymentResult(False,'INTERVENTION',2,1,False,True,False,1)
    assert set(result.public_payload())=={'schema','complete','reason','cards_total','spells_consumed',
                                        'own_exit','home_verified','intervention_free','remaining'}


def test_action_labels_are_exact_existing_subset_no_placement_switch():
    from clash_rush_rebuild.mvp_deployment import Action
    assert {x.value for x in Action}=={'ATTACK_NAVIGATION','TROOP_DEPLOYMENT','RETURN_HOME','CLEANUP_RELEASE'}
    for file in FILES:
        assert 'PLACEMENT_ENABLED = True' not in file.read_text()
