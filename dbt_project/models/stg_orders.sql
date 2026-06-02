with source_data as (
    select 101 as order_id, 1 as user_id, 99.99 as amount, '2026-06-01' as order_date
    union all
    select 102 as order_id, 2 as user_id, 49.50 as amount, '2026-06-02' as order_date
    union all
    select 103 as order_id, 1 as user_id, 15.00 as amount, '2026-06-02' as order_date
)
select * from source_data
