with users as (
    select * from {{ ref('stg_users') }}
),
orders as (
    select 
        user_id,
        count(order_id) as total_orders,
        sum(amount) as total_spent
    from {{ ref('stg_orders') }}
    group by 1
)
select 
    u.id as user_id,
    u.name,
    u.country,
    coalesce(o.total_orders, 0) as total_orders,
    coalesce(o.total_spent, 0.0) as total_spent
from users u
left join orders o on u.id = o.user_id
