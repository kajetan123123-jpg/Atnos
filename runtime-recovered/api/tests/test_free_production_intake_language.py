import pytest
from atnos.production.project_profile import ProductionOrder

FREE = ['Zrób tę rolkę bezpłatnie.', 'Wyłącznie darmowe narzędzia.',
        'Budżet 0 USD.', 'Budget $0.', 'Use free tools only.', 'No paid services.',
        'Bez płatnych usług.', 'Bez kosztów API.']

@pytest.mark.parametrize('text', FREE)
def test_free_request_cannot_dispatch_default_paid_voice(text):
    order = ProductionOrder(topic='Test request', direction=text, delivery_mode='local_only')
    with pytest.raises(ValueError, match='Ryan B'):
        order.validate_automatic_submission()

@pytest.mark.parametrize('text', FREE)
def test_explicit_local_choice_preserves_free_request(text):
    ProductionOrder(topic='Test request', direction=text, delivery_mode='local_only',
        voice_candidate='Ryan-B', provider_policy='strict_local').validate_automatic_submission()

@pytest.mark.parametrize('text', ['Explain free cash flow.', 'Opowiedz o darmowych próbkach.',
                               'Stocks with a zero-cost product.', 'Budget $100.', 'Budget $0.50.', 'Budżet 0,25 USD.'])
def test_topic_about_free_products_is_not_itself_a_free_production_constraint(text):
    ProductionOrder(topic='Test request', direction=text).validate_automatic_submission()
