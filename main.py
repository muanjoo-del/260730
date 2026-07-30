# -*- coding: utf-8 -*-
"""
전국 시군구별 청소년(13~18세, 중·고등학생 나이) 인구 비율 지도
- 인구 데이터: 읍·면·동 단위 → 코드 앞 5자리로 시군구 단위로 합산
- 지도 경계: 시군구 GeoJSON
- 지역 매칭은 이름이 아니라 '코드'로 수행 (동명이인 시군구 문제 방지)
- 청소년 비율뿐 아니라, 같은 연령대의 남녀 성비도 함께 계산
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
st.set_page_config(page_title="전국 청소년 인구 지도", layout="wide")

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
# 시군구 단위로 청소년 비율 + 성비 계산
# -----------------------------
@st.cache_data
def calc_teen_ratio(df):
    df = df.copy()
    # 코드는 행정동(읍면동) 코드이므로, 앞 5자리를 잘라 시군구 코드로 사용
    df["시군구코드"] = df["코드"].str[:5]

    # 최신 연도만 사용
    latest_year = df["연도"].max()
    df_latest = df[df["연도"] == latest_year].copy()

    # 계/남/여 각각 나이별 열 찾기
    total_age_map = get_age_columns(df_latest, "계")
    male_age_map = get_age_columns(df_latest, "남")
    female_age_map = get_age_columns(df_latest, "여")

    # 전체 인구를 구하기 위한 열(모든 나이)
    all_total_cols = list(total_age_map.keys())

    # 청소년(13~18세) 나이에 해당하는 열만 골라내기
    def teen_cols(age_map):
        return [c for c, age in age_map.items() if TEEN_MIN_AGE <= age <= TEEN_MAX_AGE]

    teen_total_cols = teen_cols(total_age_map)
    teen_male_cols = teen_cols(male_age_map)
    teen_female_cols = teen_cols(female_age_map)

    # 필요한 값들을 각 행(읍면동)에 대해 계산
    df_latest["전체인구"] = df_latest[all_total_cols].sum(axis=1)
    df_latest["청소년인구"] = df_latest[teen_total_cols].sum(axis=1)
    df_latest["남청소년인구"] = df_latest[teen_male_cols].sum(axis=1)
    df_latest["여청소년인구"] = df_latest[teen_female_cols].sum(axis=1)

    # 시군구 단위로 합산
    grouped = (
        df_latest.groupby("시군구코드")[
            ["전체인구", "청소년인구", "남청소년인구", "여청소년인구"]
        ]
        .sum()
        .reset_index()
    )

    # 청소년 비율 (전체 인구 대비)
    grouped["청소년비율"] = (grouped["청소년인구"] / grouped["전체인구"] * 100).round(2)

    # 성비: 여자 100명당 남자 수 (통계청에서 흔히 쓰는 방식)
    # 여청소년인구가 0인 곳은 계산 불가하므로 결측치로 둔다.
    grouped["성비"] = np.where(
        grouped["여청소년인구"] > 0,
        (grouped["남청소년인구"] / grouped["여청소년인구"] * 100).round(1),
        np.nan,
    )

    return grouped, latest_year


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
# 색상 구간(5단계) 자동 계산: 데이터를 실제로 다섯 덩어리로 나눈다
# -----------------------------
def make_quantile_bins(series, n_bins=5):
    """청소년 비율 값을 20/40/60/80 백분위수로 잘라
    (경계값 리스트, 구간 이름 리스트)를 돌려준다."""
    clean = series.dropna()
    quantiles = [i / n_bins for i in range(1, n_bins)]  # [0.2, 0.4, 0.6, 0.8]
    cut_points = clean.quantile(quantiles).round(1).tolist()

    edges = [-np.inf] + cut_points + [np.inf]

    labels = []
    for i in range(n_bins):
        if i == 0:
            labels.append(f"{cut_points[0]}% 미만")
        elif i == n_bins - 1:
            labels.append(f"{cut_points[-1]}% 이상")
        else:
            labels.append(f"{cut_points[i - 1]}% ~ {cut_points[i]}%")

    return edges, labels


# -----------------------------
# 메인 앱
# -----------------------------
def main():
    st.title("🧑‍🎓 전국 시군구별 청소년 인구 지도")
    st.caption(
        f"중·고등학생 나이({TEEN_MIN_AGE}~{TEEN_MAX_AGE}세) 인구 비율과 남녀 성비를 시군구 단위로 표시합니다."
    )

    # 데이터 로딩
    with st.spinner("데이터를 불러오는 중입니다..."):
        pop_df = load_population()
        geojson = load_geojson()

    ratio_df, latest_year = calc_teen_ratio(pop_df)
    geo_df = geojson_to_dataframe(geojson)

    # 지도용 데이터: GeoJSON의 코드를 기준으로 인구 데이터를 붙인다 (코드로 매칭!)
    merged = geo_df.merge(ratio_df, left_on="코드", right_on="시군구코드", how="left")

    # 청소년 비율을 5단계 구간으로 자동으로 나누기 (실제 데이터를 다섯 덩어리로 분할)
    bin_edges, bin_labels = make_quantile_bins(merged["청소년비율"], n_bins=5)
    merged["구간"] = pd.cut(
        merged["청소년비율"], bins=bin_edges, labels=bin_labels, right=False
    )

    st.subheader(f"{latest_year}년 기준 시군구별 청소년({TEEN_MIN_AGE}~{TEEN_MAX_AGE}세) 인구 비율")

    # -----------------------------
    # 단계구분도 그리기
    # -----------------------------
    fig = px.choropleth(
        merged,
        geojson=geojson,
        locations="코드",                # merged의 '코드' 열 값으로
        featureidkey="properties.코드",   # geojson의 properties.코드 값과 매칭
        color="구간",
        category_orders={"구간": bin_labels},
        color_discrete_map=dict(zip(bin_labels, BIN_COLORS)),
        hover_name="시군구",
        hover_data={
            "시도": True,
            "청소년비율": ":.1f",
            "성비": ":.1f",
            "코드": False,   # 코드는 마우스오버에 안 보이게
            "구간": False,
        },
        labels={
            "청소년비율": "청소년 비율(%)",
            "시도": "시도",
            "구간": "구간",
            "성비": "성비(여 100명당 남)",
        },
    )

    # 배경 지도(타일) 없이 경계선만 보이도록 설정
    fig.update_geos(
        fitbounds="locations",
        visible=False,          # 기본 세계지도 배경 숨기기
    )
    fig.update_traces(marker_line_color="white", marker_line_width=0.5)
    fig.update_layout(
        margin={"r": 0, "t": 10, "l": 0, "b": 0},
        legend_title_text=f"청소년({TEEN_MIN_AGE}~{TEEN_MAX_AGE}세) 비율",
        height=650,
    )

    st.plotly_chart(fig, use_container_width=True)

    # -----------------------------
    # 상위 10 / 하위 10 표 (청소년 비율 + 성비 함께 표시)
    # -----------------------------
    st.subheader("청소년 비율 상위 10 / 하위 10 시군구")
    st.caption("성비는 '여자 청소년 100명당 남자 청소년 수'입니다. (100보다 크면 남자가 더 많음)")

    table_df = merged.dropna(subset=["청소년비율"]).copy()
    table_df["청소년비율(%)"] = table_df["청소년비율"].round(1)
    table_df["성비"] = table_df["성비"].round(1)

    display_cols = ["시도", "시군구", "청소년비율(%)", "성비"]

    top10 = (
        table_df.sort_values("청소년비율", ascending=False)
        .head(10)[display_cols]
        .reset_index(drop=True)
    )
    bottom10 = (
        table_df.sort_values("청소년비율", ascending=True)
        .head(10)[display_cols]
        .reset_index(drop=True)
    )

    # 순위가 1부터 보이도록 인덱스 조정
    top10.index = top10.index + 1
    bottom10.index = bottom10.index + 1

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**🔺 청소년 비율 높은 시군구 TOP 10**")
        st.dataframe(top10, use_container_width=True)
    with col2:
        st.markdown("**🔻 청소년 비율 낮은 시군구 TOP 10**")
        st.dataframe(bottom10, use_container_width=True)


if __name__ == "__main__":
    main()
