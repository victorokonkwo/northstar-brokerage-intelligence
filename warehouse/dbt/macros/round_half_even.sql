{#- Round a numeric expression half-even (banker's rounding) to `scale` places.
    ADR 004 makes ROUND_HALF_EVEN the only rounding mode, and Postgres round()
    rounds halves away from zero, so reporting outputs round through this macro.
    floor() keeps it correct for negative values: -2.5 -> -2, -3.5 -> -4. -#}
{% macro round_half_even(expression, scale) -%}
    {%- set factor = 10 ** scale -%}
    {%- set scaled = "((" ~ expression ~ ") * " ~ factor ~ ")" -%}
    (
        case
            when {{ scaled }} - floor({{ scaled }}) > 0.5 then floor({{ scaled }}) + 1
            when {{ scaled }} - floor({{ scaled }}) < 0.5 then floor({{ scaled }})
            when mod(floor({{ scaled }}), 2) = 0 then floor({{ scaled }})
            else floor({{ scaled }}) + 1
        end / {{ factor }}
    )::numeric(18, {{ scale }})
{%- endmacro %}

{#- A ratio of two non-negative integer counts, rounded half-even to `scale`
    places, in integer arithmetic only. Dividing decimals isn't portable: on
    Redshift numeric(38, 0) / integer keeps just 4 decimal places (precision is
    capped at 38, docs/sources.md), so 17/18 would round to 0.944400, not
    0.944444. Here the quotient and remainder of numerator * 10^scale by the
    denominator are exact (integer division truncates, which is the floor for
    non-negative values), and twice the remainder against the denominator
    decides the rounding. A zero denominator fails, as a division would. -#}
{% macro count_ratio_half_even(numerator, denominator, scale) -%}
    {%- set n = "(cast(" ~ numerator ~ " as bigint) * " ~ (10 ** scale) ~ ")" -%}
    {%- set d = "cast(" ~ denominator ~ " as bigint)" -%}
    {%- set q = "(" ~ n ~ " / " ~ d ~ ")" -%}
    {%- set twice_r = "(2 * (" ~ n ~ " - " ~ q ~ " * " ~ d ~ "))" -%}
    (
        cast(
            case
                when {{ twice_r }} > {{ d }} then {{ q }} + 1
                when {{ twice_r }} < {{ d }} then {{ q }}
                when mod({{ q }}, 2) = 0 then {{ q }}
                else {{ q }} + 1
            end as numeric(18, 0)
        ) * {{ "0." ~ ("0" * (scale - 1)) ~ "1" }}
    )::numeric(18, {{ scale }})
{%- endmacro %}

{#- A ratio of two non-negative money amounts with 2 decimal places, rounded
    half-even to `scale` places. Exact, like count_ratio_half_even: both amounts
    are whole numbers of cents, so the ratio is a ratio of integer counts.
    Dividing the decimals directly isn't portable: Redshift keeps just 4
    decimal places for numeric(18, 2) / numeric(18, 2) (found by the 4F-c
    parity run). A null denominator gives null. -#}
{% macro amount_ratio_half_even(numerator, denominator, scale) -%}
    {{ count_ratio_half_even(
        "round((" ~ numerator ~ ") * 100)",
        "round((" ~ denominator ~ ") * 100)",
        scale,
    ) }}
{%- endmacro %}
