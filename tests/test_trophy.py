from api.models import UserGetData, Card, AreaItem
from api.trophy import showroom_response


def test_empty_account_has_valid_showroom_and_eight_trophy_slots():
    response = showroom_response(UserGetData())
    assert response['user_data'] == dict(master_trophy_grade_id=1, total_master_trophy_detail_point=0)
    assert {x['master_trophy_id'] for x in response['trophy_list']} == set(range(1,9))
    assert all(x['master_trophy_grade_id']==1 and x['count']==0 for x in response['trophy_list'])


def test_trophy_thresholds_use_distinct_owned_content_without_mutation():
    user = UserGetData(card_list=[Card(id=i+1, master_card_id=i) for i in range(50)],
                       area_item_list=[AreaItem(id=i+1, master_area_item_id=123) for i in range(150)])
    user.card_list.append(Card(id=100, master_card_id=1))
    before = user.to_dict()
    response = showroom_response(user)
    cards, furniture = response['trophy_list'][0], response['trophy_list'][3]
    assert (cards['count'], cards['master_trophy_grade_id'], cards['master_trophy_detail_point']) == (50,2,10)
    assert (furniture['count'], furniture['master_trophy_grade_id']) == (1,1)
    assert response['user_data']['total_master_trophy_detail_point'] == 10
    assert user.to_dict() == before
