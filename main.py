# -*- coding: utf-8 -*-
"""
전국 시군구별 청소년(13~18세, 중·고등학생 나이) 인구수 - 연도별 변화 애니메이션 지도
- 비율(%)이 아니라 실제 인구수(명)를 기준으로 색을 칠한다.
- 연도(2015~2026)를 슬라이더로 움직이면서 지도 색이 바뀌는 애니메이션.
- 인구 데이터: 읍·면·동 단위 → 코드 앞 5자리로 시군구 단위로 합산
- 지도 경계: 시군구 GeoJSON
- 지역 매칭은 이름이 아니라 '코드'로 수행 (동명이인 시군구 문제 방지)
"""

import re

import requests
import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px

# -----------------------------
# 기본 설정
# -----------------------------
st.set_page_config(page_title="전국 청소년 인구수 변화 지도", layout="wide")

POPULATION_URL = (
    "https://raw.githubusercontent.com/greatsong/modudata/main/data/population_yearly.csv.gz"
)
GEOJSON_URL = (
    "https://raw.githubusercontent.com/greatsong/modudata/main/data/boundaries/sigungu_kr.geojson"
)

# 청소년으로 볼 나이 범위: 중학생(13~15세) ~ 고등학생(16~18세)
TEEN_MIN_AGE = 13
TEEN_MAX_AGE = 18

# 낮은 쪽은 옅게, 높은 쪽은 진하게 (초록색 5단계)
BIN_COLORS = ["#edf8e9", "#bae4b3", "#74c476", "#31a354", "#006d2c"]


# -----------------------------
# 데이터 불러오기 (캐시 사용: 앱이 다시 실행돼도 매번 새로 안 받아옴)
# -----------------------------
@st.cache_data
def load_population():
    """인구 데이터를 읽어온다. '코드' 열은 반드시 문자열(글자)로 읽어야
    앞자리가 0인 코드가 숫자로 변환되면서 깨지는 것을 막을 수 있다."""
    df = pd.read_csv(POPULATION_URL, compression="gzip", dtype={"코드": str})
    return df


@st.cache_data
def load_geojson():
    """시군구 경계 GeoJSON을 읽어온다."""
    res = requests.get(GEOJSON_URL)
    res.raise_for_status()
    return res.json()


# -----------------------------
# 나이별 인구 열 찾기 (계_ / 남_ / 여_ 공통으로 쓸 수 있는 함수)
# -----------------------------
def get_age_columns(df, prefix):
    """예: prefix='계' 이면 '계_0세' ~ '계_100세 이상' 열들을 찾아
    {열이름: 나이(정수)} 형태의 딕셔너리로 돌려준다."""
    target_cols = [c for c in df.columns if c.startswith(f"{prefix}_")]
    age_map = {}
    for c in target_cols:
        digits = re.sub(r"\D", "", c)  # 숫자만 남기기 (예: '100세 이상' -> '100')
        if digits != "":
            age_map[c] = int(digits)
    return age_map


# -----------------------------
# 시군구 x 연도 단위로 청소년 인구수 + 성비 계산 (전체 연도!)
# -----------------------------
@st.cache_data
def calc_teen_population_all_years(df):
    df = df.copy()
    # 코드는 행정동(읍면동) 코드이므로, 앞 5자리를 잘라 시군구 코드로 사용
    df["시군구코드"] = df["코드"].str[:5]

    # 계/남/여 각각 나이별 열 찾기 (연도가 달라져도 열 이름 구조는 동일하다고 가정)
    total_age_map = get_age_columns(df, "계")
    male_age_map = get_age_columns(df, "남")
    female_age_map = get_age_columns(df, "여")

    all_total_cols = list(total_age_map.keys())

    def teen_cols(age_map):
        return [c for c, age in age_map.items() if TEEN_MIN_AGE <= age <= TEEN_MAX_AGE]

    teen_total_cols = teen_cols(total_age_map)
    teen_male_cols = teen_cols(male_age_map)
    teen_female_cols = teen_cols(female_age_map)

    # 행(읍면동 x 연도) 단위로 값 계산
    df["전체인구"] = df[all_total_cols].sum(axis=1)
    df["청소년인구"] = df[teen_total_cols].sum(axis=1)
    df["남청소년인구"] = df[teen_male_cols].sum(axis=1)
    df["여청소년인구"] = df[teen_female_cols].sum(axis=1)

    # 연도 + 시군구 단위로 합산
    grouped = (
        df.groupby(["연도", "시군구코드"])[
            ["전체인구", "청소년인구", "남청소년인구", "여청소년인구"]
        ]
        .sum()
        .reset_index()
    )

    grouped["청소년비율"] = (grouped["청소년인구"] / grouped["전체인구"] * 100).round(2)
    grouped["성비"] = np.where(
        grouped["여청소년인구"] > 0,
        (grouped["남청소년인구"] / grouped["여청소년인구"] * 100).round(1),
        np.nan,
    )

    return grouped


# -----------------------------
# GeoJSON 속성(코드·시군구·시도)을 표로 변환
# -----------------------------
def geojson_to_dataframe(geojson):
    rows = []
    for feature in geojson["features"]:
        prop = feature["properties"]
        rows.append(
            {
                "코드": str(prop.get("코드")),
                "시군구": prop.get("시군구"),
                "시도": prop.get("시도"),
            }
        )
    return pd.DataFrame(rows)


# -----------------------------
# 색상 구간(5단계) 자동 계산: 모든 연도를 합쳐서 다섯 덩어리로 나눈다
# (연도마다 기준이 바뀌면 애니메이션에서 색 의미가 흔들리므로, 전체 기간 기준 1번만 계산)
# -----------------------------
def make_quantile_bins(series, n_bins=5):
    clean = series.dropna()
    quantiles = [i / n_bins for i in range(1, n_bins)]  # [0.2, 0.4, 0.6, 0.8]
    cut_points = clean.quantile(quantiles).round(0).astype(int).tolist()

    edges = [-np.inf] + cut_points + [np.inf]

    def fmt(n):
        return f"{n:,}명"

    labels = []
    for i in range(n_bins):
        if i == 0:
            labels.append(f"{fmt(cut_points[0])} 미만")
        elif i == n_bins - 1:
            labels.append(f"{fmt(cut_points[-1])} 이상")
        else:
            labels.append(f"{fmt(cut_points[i - 1])} ~ {fmt(cut_points[i])}")

    return edges, labels


# -----------------------------
# 메인 앱
# -----------------------------
def main():
    st.title("🧑‍🎓 전국 시군구별 청소년 인구수 변화 애니메이션 지도")
    st.caption(
        f"중·고등학생 나이({TEEN_MIN_AGE}~{TEEN_MAX_AGE}세) 인구수가 연도에 따라 "
        "어떻게 바뀌는지 지도에서 확인할 수 있습니다. 아래 재생▶ 버튼을 누르거나 슬라이더를 움직여 보세요."
    )

    # 데이터 로딩
    with st.spinner("데이터를 불러오는 중입니다..."):
        pop_df = load_population()
        geojson = load_geojson()

    teen_all_years = calc_teen_population_all_years(pop_df)
    geo_df = geojson_to_dataframe(geojson)

    # 지도용 데이터: GeoJSON의 코드를 기준으로 인구 데이터를 붙인다 (코드로 매칭!)
    # 시군구 하나당 연도 수(예: 12개)만큼 행이 여러 개 생긴다.
    merged = geo_df.merge(teen_all_years, left_on="코드", right_on="시군구코드", how="left")
    merged = merged.dropna(subset=["연도"]).copy()
    merged["연도"] = merged["연도"].astype(int)

    years_sorted = sorted(merged["연도"].unique().tolist())

    # 색상 구간은 전체 연도를 합친 값을 기준으로 한 번만 계산 (애니메이션 중 기준 유지)
    bin_edges, bin_labels = make_quantile_bins(merged["청소년인구"], n_bins=5)
    merged["구간"] = pd.cut(
        merged["청소년인구"], bins=bin_edges, labels=bin_labels, right=False
    )

    # -----------------------------
    # 애니메이션 단계구분도
    # -----------------------------
    st.subheader(f"{years_sorted[0]}년 ~ {years_sorted[-1]}년 청소년({TEEN_MIN_AGE}~{TEEN_MAX_AGE}세) 인구수 변화")

    fig = px.choropleth(
        merged,
        geojson=geojson,
        locations="코드",
        featureidkey="properties.코드",
        color="구간",
        animation_frame="연도",
        category_orders={"연도": years_sorted, "구간": bin_labels},
        color_discrete_map=dict(zip(bin_labels, BIN_COLORS)),
        hover_name="시군구",
        hover_data={
            "시도": True,
            "청소년인구": ":,",
            "성비": ":.1f",
            "청소년비율": ":.1f",
            "연도": False,   # 슬라이더에 이미 표시되므로 hover에서는 생략
            "코드": False,
            "구간": False,
        },
        labels={
            "청소년인구": "청소년 인구수(명)",
            "시도": "시도",
            "구간": "구간",
            "성비": "성비(여 100명당 남)",
            "청소년비율": "참고: 비율(%)",
        },
    )

    fig.update_geos(fitbounds="locations", visible=False)
    fig.update_traces(marker_line_color="white", marker_line_width=0.5)
    fig.update_layout(
        margin={"r": 0, "t": 10, "l": 0, "b": 0},
        legend_title_text=f"청소년({TEEN_MIN_AGE}~{TEEN_MAX_AGE}세) 인구수",
        height=650,
    )

    # 애니메이션 프레임(재생) 속도 조절: 프레임 사이 800ms
    if fig.layout.updatemenus:
        fig.layout.updatemenus[0].buttons[0].args[1]["frame"]["duration"] = 800
        fig.layout.updatemenus[0].buttons[0].args[1]["transition"]["duration"] = 300

    st.plotly_chart(fig, use_container_width=True)

    # -----------------------------
    # 특정 시군구 선택 -> 연도별 추이 선 그래프
    # -----------------------------
    st.subheader("특정 시군구의 연도별 청소년 인구 추이")

    region_options = (
        merged[["코드", "시도", "시군구"]]
        .drop_duplicates()
        .assign(표시이름=lambda d: d["시도"] + " " + d["시군구"])
        .sort_values("표시이름")
    )
    selected_display = st.selectbox("시군구를 선택하세요", region_options["표시이름"].tolist())
    selected_code = region_options.loc[
        region_options["표시이름"] == selected_display, "코드"
    ].values[0]

    trend_df = merged[merged["코드"] == selected_code].sort_values("연도")

    line_fig = px.line(
        trend_df,
        x="연도",
        y="청소년인구",
        markers=True,
        labels={"연도": "연도", "청소년인구": "청소년 인구수(명)"},
        title=f"{selected_display} 청소년({TEEN_MIN_AGE}~{TEEN_MAX_AGE}세) 인구 추이",
    )
    line_fig.update_layout(height=350, margin={"r": 20, "t": 40, "l": 20, "b": 20})
    st.plotly_chart(line_fig, use_container_width=True)

    # -----------------------------
    # 최신 연도 기준 상위 10 / 하위 10 표
    # -----------------------------
    latest_year = years_sorted[-1]
    st.subheader(f"{latest_year}년 기준 청소년 인구수 상위 10 / 하위 10 시군구")
    st.caption("성비는 '여자 청소년 100명당 남자 청소년 수'입니다. (100보다 크면 남자가 더 많음)")

    latest_df = merged[merged["연도"] == latest_year].dropna(subset=["청소년인구"]).copy()
    latest_df["청소년인구(명)"] = latest_df["청소년인구"].round(0).astype(int)
    latest_df["성비"] = latest_df["성비"].round(1)
    latest_df["비율(%)"] = latest_df["청소년비율"].round(1)

    display_cols = ["시도", "시군구", "청소년인구(명)", "성비", "비율(%)"]

    top10 = (
        latest_df.sort_values("청소년인구", ascending=False)
        .head(10)[display_cols]
        .reset_index(drop=True)
    )
    bottom10 = (
        latest_df.sort_values("청소년인구", ascending=True)
        .head(10)[display_cols]
        .reset_index(drop=True)
    )
    top10.index = top10.index + 1
    bottom10.index = bottom10.index + 1

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**🔺 청소년 인구수 많은 시군구 TOP 10**")
        st.dataframe(top10, use_container_width=True)
    with col2:
        st.markdown("**🔻 청소년 인구수 적은 시군구 TOP 10**")
        st.dataframe(bottom10, use_container_width=True)


if __name__ == "__main__":
    main()
