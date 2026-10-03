"""Selection rules from the recovered base-pool master tables."""
import random


class InsufficientTicket(ValueError):
    pass


class DrawLimitReached(ValueError):
    pass


def draw_counts(db, user_id, lottery_id, price_number, now):
    # Original LotteryInfoMst: a daily draw resets at 04:00 Japan time.
    day_start = ((now + 5 * 3600) // 86400) * 86400 - 5 * 3600
    row = db.execute('''SELECT COUNT(DISTINCT t.id),
        COUNT(DISTINCT CASE WHEN t.created_at >= ? THEN t.id END)
        FROM state_transactions t JOIN lottery_draws d ON d.transaction_id=t.id
        WHERE t.user_id=? AND d.master_lottery_id=?
          AND json_extract(t.request_json,'$.master_lottery_price_number')=?''',
        (day_start, user_id, lottery_id, price_number)).fetchone()
    return int(row[0]), int(row[1])


def select_items(items, count, rarity_weights, guaranteed_weights, rng=None):
    rng = rng or random.SystemRandom()
    by_rarity = {}
    for item in items:
        by_rarity.setdefault(int(item['rarity']), []).append(item)
    results = []
    for index in range(count):
        weights = guaranteed_weights if count == 10 and index == 9 else rarity_weights
        choices = [(int(row['rarity']), int(row['weight'])) for row in weights
                   if int(row['rarity']) in by_rarity and int(row['weight']) > 0]
        if not choices:
            raise ValueError('lottery has no eligible rewards')
        roll = rng.randrange(sum(weight for _, weight in choices))
        position = roll
        for rarity, weight in choices:
            if position < weight:
                group = by_rarity[rarity]
                results.append((rng.choice(group), roll))
                break
            position -= weight
    return results
