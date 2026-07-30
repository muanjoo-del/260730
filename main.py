# -*- coding: utf-8 -*-
"""
전국 시군구별 고령화 지도 (65세 이상 인구 비율)
- 인구 데이터: 읍·면·동 단위 → 코드 앞 5자리로 시군구 단위로 합산
- 지도 경계: 시군구 GeoJSON
- 지역 매칭은 이름이 아니라 '코드'로 수행 (동명이인 시군구 문제 방지)
"""

import re
import json

import requests
import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px

# -----------------------------
# 기본 설정
# -----------------------------
st.set_page_config(page_title="전국 고령화 지도", layout="wide")

POPULATION_URL = (
    "https://raw.githubusercontent.com/greatsong/modudata/main/data/population_yearly.csv.gz"
)
GEOJSON_URL = (
    "https://raw.githubusercontent.com/greatsong/modudata/main/data/boundaries/sigungu_kr.geojson"
)

# 색상 구간 경계값 (%) - 문제에서 지정한 값
BIN_EDGES = [-np.inf, 19, 23, 28, 38, np.inf]
BIN_LABELS = ["19% 미만", "19% ~ 23%", "23% ~ 28%", "28% ~ 38%", "38% 이상"]

# 낮은 쪽은 옅게, 높은 쪽은 진하게 (파란색 5단계)
BIN_COLORS = ["#eff3ff", "#bdd7e7", "#6baed6", "#3182bd", "#08519c"]


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
# 나이별 인구 열 찾기
# -----------------------------
def get_age_columns(df):
    """'계_0세' ~ '계_100세 이상' 형태의 열 이름에서 나이(정수)를 뽑아낸다."""
    age_cols = [c for c in df.columns if c.startswith("계_")]
    age_map = {}  # {열이름: 나이}
    for c in age_cols:
        # 숫자만 추출 (예: '계_100세 이상' -> '100', '계_5세' -> '5')
        digits = re.sub(r"\D", "", c)
        if digits != "":
            age_map[c] = int(digits)
    return age_map


# -----------------------------
# 시군구 단위로 65세 이상 비율 계산
# -----------------------------
@st.cache_data
def calc_elderly_ratio(df):
    # 코드는 행정동(읍면동) 코드이므로, 앞 5자리를 잘라 시군구 코드로 사용
    df = df.copy()
    df["시군구코드"] = df["코드"].str[:5]

    # 최신 연도만 사용
    latest_year = df["연도"].max()
    df_latest = df[df["연도"] == latest_year].copy()

    # 나이별 열(계_ 로 시작하는 열들) 찾기
    age_map = get_age_columns(df_latest)
    all_age_cols = list(age_map.keys())
    elder_cols = [c for c, age in age_map.items() if age >= 65]

    # 전체 인구, 65세 이상 인구 계산
    df_latest["전체인구"] = df_latest[all_age_cols].sum(axis=1)
    df_latest["고령인구"] = df_latest[elder_cols].sum(axis=1)

    # 시군구 단위로 합산
    grouped = (
        df_latest.groupby("시군구코드")[["전체인구", "고령인구"]]
        .sum()
        .reset_index()
    )
    grouped["고령화율"] = (grouped["고령인구"] / grouped["전체인구"] * 100).round(2)

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
# 메인 앱
# -----------------------------
def main():
    st.title("🗺️ 전국 시군구별 고령화 지도")
    st.caption("65세 이상 인구 비율(고령화율)을 시군구 단위로 표시합니다.")

    # 데이터 로딩
    with st.spinner("데이터를 불러오는 중입니다..."):
        pop_df = load_population()
        geojson = load_geojson()

    ratio_df, latest_year = calc_elderly_ratio(pop_df)
    geo_df = geojson_to_dataframe(geojson)

    # 지도용 데이터: GeoJSON의 코드를 기준으로 인구 데이터를 붙인다 (코드로 매칭!)
    merged = geo_df.merge(ratio_df, left_on="코드", right_on="시군구코드", how="left")

    # 5단계 구간으로 나누기
    merged["구간"] = pd.cut(
        merged["고령화율"], bins=BIN_EDGES, labels=BIN_LABELS, right=False
    )

    st.subheader(f"{latest_year}년 기준 시군구별 고령화율")

    # -----------------------------
    # 단계구분도 그리기
    # -----------------------------
    fig = px.choropleth(
        merged,
        geojson=geojson,
        locations="코드",                # merged의 '코드' 열 값으로
        featureidkey="properties.코드",   # geojson의 properties.코드 값과 매칭
        color="구간",
        category_orders={"구간": BIN_LABELS},
        color_discrete_map=dict(zip(BIN_LABELS, BIN_COLORS)),
        hover_name="시군구",
        hover_data={
            "시도": True,
            "고령화율": ":.1f",
            "코드": False,   # 코드는 마우스오버에 안 보이게
            "구간": False,
        },
        labels={"고령화율": "고령화율(%)", "시도": "시도", "구간": "구간"},
    )

    # 배경 지도(타일) 없이 경계선만 보이도록 설정
    fig.update_geos(
        fitbounds="locations",
        visible=False,          # 기본 세계지도 배경 숨기기
    )
    fig.update_traces(marker_line_color="white", marker_line_width=0.5)
    fig.update_layout(
        margin={"r": 0, "t": 10, "l": 0, "b": 0},
        legend_title_text="65세 이상 인구 비율",
        height=650,
    )

    st.plotly_chart(fig, use_container_width=True)

    # -----------------------------
    # 상위 10 / 하위 10 표
    # -----------------------------
    st.subheader("고령화율 상위 10 / 하위 10 시군구")

    table_df = merged.dropna(subset=["고령화율"]).copy()
    table_df["고령화율(%)"] = table_df["고령화율"].round(1)

    top10 = (
        table_df.sort_values("고령화율", ascending=False)
        .head(10)[["시도", "시군구", "고령화율(%)"]]
        .reset_index(drop=True)
    )
    bottom10 = (
        table_df.sort_values("고령화율", ascending=True)
        .head(10)[["시도", "시군구", "고령화율(%)"]]
        .reset_index(drop=True)
    )

    # 순위가 1부터 보이도록 인덱스 조정
    top10.index = top10.index + 1
    bottom10.index = bottom10.index + 1

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**🔺 고령화율 높은 시군구 TOP 10**")
        st.dataframe(top10, use_container_width=True)
    with col2:
        st.markdown("**🔻 고령화율 낮은 시군구 TOP 10**")
        st.dataframe(bottom10, use_container_width=True)


if __name__ == "__main__":
    main()
