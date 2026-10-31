import pytest
from PIL import Image

from atnos.production import inflation_covers,retirement_covers


def inflation_claims():
    return [
        {"id":"deposit_interest","operation":"percent","operands":[1000,2],"result":20,"unit":"PLN","spoken_result":"twenty","assumptions":"An illustrative annual interest rate."},
        {"id":"price_increase","operation":"percent","operands":[1000,5],"result":50,"unit":"PLN","spoken_result":"fifty","assumptions":"An illustrative annual price increase."},
        {"id":"real_purchasing_loss","operation":"product","operands":[1-1.02/1.05,100],"result":(1-1.02/1.05)*100,"unit":"percent","spoken_result":"two point nine","assumptions":"A real purchasing-power loss before tax."},
    ]


@pytest.mark.parametrize("module",[inflation_covers,retirement_covers])
def test_financial_documents_render_both_delivery_sizes(module,tmp_path):
    paths=module.create(tmp_path,headline="Compare the account conditions",detail="Understand the next step",claims=inflation_claims())
    for path,size in zip(paths,[(1080,1920),(1280,720)],strict=True):
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            assert image.size == size
            assert len(image.getcolors(image.width*image.height)) > 20


def test_inflation_cover_refuses_invented_results(tmp_path):
    claims=inflation_claims()
    claims[0]["result"]=90
    with pytest.raises(ValueError,match="verified"):
        inflation_covers.create(tmp_path,headline="Example",detail="Example",claims=claims)
