from django.http import HttpResponse
from sqlalchemy import create_engine
from django.shortcuts import render
import json
import six
from .charts import *
import datetime
from io import BytesIO as IO
from django.views.decorators.cache import cache_page
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from django.shortcuts import redirect
from statsmodels.tsa.arima.model import ARIMA
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
import warnings
warnings.filterwarnings("ignore", category=FutureWarning, module="sklearn")

ENGINE = create_engine('mysql+pymysql://root:Su.040121@localhost/res')

DB_TABLE = "data"

sql1 = f"SELECT * FROM {DB_TABLE}"
df1 = pd.read_sql_query(sql1, ENGINE)

# 退出登录页面
def logout_view(request):
    if request.method == "POST":
        logout(request)  # 注销用户
        return redirect('login')  # 重定向到登录页面
    else:
        return redirect('index')  # 如果是 GET 请求，则重定向到首页（可选）


# 初步查询，处理单选字段
def sqlparse(context):
    print(context)
    sql = "Select * from %s Where PERIOD = '%s' And UNIT = '%s'" % \
          (DB_TABLE, context['PERIOD_select'][0], context['UNIT_select'][0])  # 先处理单选部分

    # 下面循环处理多选部分
    for k, v in context.items():
        if k not in ['csrfmiddlewaretoken', 'DIMENSION_select', 'PERIOD_select', 'UNIT_select']:
            if k[-2:] == '[]':
                field_name = k[:-9]  # 如果键以[]结尾，删除_select[]取原字段名
            else:
                field_name = k[:-7]  # 如果键不以[]结尾，删除_select取原字段名
            selected = v  # 选择项
            # 调用
            sql = sql_extent(sql, field_name, selected)
    return sql


# 多选拼接
def sql_extent(sql, field_name, selected, operator=" AND "):
    if selected is not None:
        statement = ''
        for data in selected:
            statement = statement + "'" + data + "', "
        statement = statement[:-2]
        if statement != '':
            sql = sql + operator + field_name + " in (" + statement + ")"
    return sql


# 整体市场规模、增长率、CAGR（年复合增长率）--
def get_kpi(df):
    # 按列求和为市场总值的Series
    market_total = df.sum(axis=1)
    # 最后一行（最后一个DATE）就是最新的市场规模
    market_size = market_total.iloc[-1]
    # 市场按列求和，倒数第5行（倒数第5个DATE）就是同比的市场规模，可以用来求同比增长率
    market_gr = market_total.iloc[-1] / market_total.iloc[-5] - 1
    # 因为数据第一年是四年前的同期季度，时间序列收尾相除后开四次方根可得到年复合增长率
    market_cagr = (market_total.iloc[-1] / market_total.iloc[0]) ** (0.25) - 1
    if market_size == np.inf or market_size == -np.inf:
        market_size = "N/A"
    if market_gr == np.inf or market_gr == -np.inf:
        market_gr = "N/A"
    if market_cagr == np.inf or market_cagr == -np.inf:
        market_cagr = "N/A"

    return {
        "market_size": market_size,
        "market_gr": market_gr,
        "market_cagr": market_cagr,
    }


# 市场规模
def ptable(df):
    # 份额
    df_share = df.transform(lambda x: x / x.sum(), axis=1)

    # 同比增长率，要考虑分子为0的问题
    df_gr = df.pct_change(periods=4)
    df_gr.dropna(how='all', inplace=True)
    df_gr.replace([np.inf, -np.inf], np.nan, inplace=True)

    # 最新滚动年绝对值表现及同比净增长
    df_latest = df.iloc[-1, :]
    df_latest_diff = df.iloc[-1, :] - df.iloc[-5, :]

    # 最新滚动年份额表现及同比份额净增长
    df_share_latest = df_share.iloc[-1, :]
    df_share_latest_diff = df_share.iloc[-1, :] - df_share.iloc[-5, :]

    # 进阶指标EI，衡量与市场增速的对比，高于100则为跑赢大盘
    df_gr_latest = df_gr.iloc[-1, :]
    df_total_gr_latest = df.sum(axis=1).iloc[-1] / df.sum(axis=1).iloc[-5] - 1
    df_ei_latest = (df_gr_latest + 1) / (df_total_gr_latest + 1) * 100

    df_combined = pd.concat(
        [df_latest, df_latest_diff, df_share_latest, df_share_latest_diff, df_gr_latest, df_ei_latest], axis=1)
    df_combined.columns = ['最新滚动年销售额',
                           '净增长',
                           '份额',
                           '份额同比变化',
                           '同比增长率',
                           'EI']

    return df_combined


pd.set_option('display.max_columns', None)

# print(ptable(pivoted))
# print(get_kpi(pivoted))

# 该字典key为前端准备显示的所有多选字段名, value为数据库对应的字段名
D_MULTI_SELECT = {
    'TC I': '[TC I]',
    'TC II': '[TC II]',
    'TC III': '[TC III]',
    'TC IV': '[TC IV]',
    '通用名|MOLECULE': 'MOLECULE',
    '商品名|PRODUCT': 'PRODUCT',
    '包装|PACKAGE': 'PACKAGE',
    '生产企业|CORPORATION': 'CORPORATION',
    '企业类型': 'MANUF_TYPE',
    '剂型': 'FORMULATION',
    '剂量': 'STRENGTH'
}


# 进行数据透视表
def get_df(form_dict, is_pivoted=True):
    sql = sqlparse(form_dict)  # sql拼接
    df = pd.read_sql_query(sql, ENGINE)  # 将sql语句结果读取至Pandas Dataframe

    if is_pivoted is True:
        dimension_selected = form_dict['DIMENSION_select'][0]
        if dimension_selected[0] == '[':

            column = dimension_selected[1:][:-1]
        else:
            column = dimension_selected

        pivoted = pd.pivot_table(df,
                                 values='AMOUNT',  # 数据透视汇总值为AMOUNT字段，一般保持不变
                                 index='DATE',  # 数据透视行为DATE字段，一般保持不变
                                 columns=column,  # 数据透视列为前端选择的分析维度
                                 aggfunc=np.sum)  # 数据透视汇总方式为求和，一般保持不变
        if pivoted.empty is False:
            pivoted.sort_values(by=pivoted.index[-1], axis=1, ascending=False, inplace=True)  # 结果按照最后一个DATE表现排序

        return pivoted
    else:
        return df


@login_required
@cache_page(60 * 60 * 24 * 30)  # 缓存30天
def query(request):
    form_dict = dict(six.iterlists(request.GET))
    pivoted = get_df(form_dict)

    table = ptable(pivoted)
    table = table.to_html(formatters=build_formatters_by_col(table),
                          classes='ui selectable celled table',
                          table_id='ptable')

    # KPI
    kpi = get_kpi(pivoted)

    # Pyecharts交互图表
    bar_total_trend = json.loads(prepare_chart(pivoted, 'bar_total_trend', form_dict))

    # Matplotlib静态图表
    bubble_performance = prepare_chart(pivoted, 'bubble_performance', form_dict)

    predict_chart = prepare_chart(pivoted, 'predict_chart', form_dict)

    group_chart = prepare_chart(pivoted, 'group_chart', form_dict)

    context = {
        "market_size": kpi["market_size"],
        "market_gr": kpi["market_gr"],
        "market_cagr": kpi["market_cagr"],
        'ptable': table,
        'bar_total_trend': bar_total_trend,
        'bubble_performance': bubble_performance,
        'predict_chart': predict_chart,
        'group_chart': group_chart
    }

    return HttpResponse(json.dumps(context, ensure_ascii=False),
                        content_type="application/json charset=utf-8")  # 返回结果必须是json格式


# 调整表格格式
def build_formatters_by_col(df):
    format_abs = lambda x: '{:,.0f}'.format(x)
    format_share = lambda x: '{:.1%}'.format(x)
    format_gr = lambda x: '{:.1%}'.format(x)
    format_currency = lambda x: '¥{:,.0f}'.format(x)
    d = {}
    for column in df.columns:
        if '份额' in column or '贡献' in column:
            d[column] = format_share
        elif '价格' in column or '单价' in column:
            d[column] = format_currency
        elif '同比增长' in column or '增长率' in column or 'CAGR' in column or '同比变化' in column:
            d[column] = format_gr
        else:
            d[column] = format_abs
    return d


# 搜索框
@login_required
@cache_page(60 * 60 * 24 * 30)
def search(request, column, kw):
    # 使用 MySQL 支持的 LIMIT 语法
    sql = f"SELECT DISTINCT {column} FROM {DB_TABLE} WHERE {column} LIKE %s LIMIT 10"
    try:
        # 执行查询时传入参数 kw，确保 params 是元组
        df = pd.read_sql_query(sql, ENGINE, params=(f"%{kw}%",))  # 参数为元组
        l = df.values.flatten().tolist()
        results_list = []
        for element in l:
            option_dict = {'name': element,
                           'value': element,
                           }
            results_list.append(option_dict)
        res = {
            "success": True,
            "results": results_list,
            "code": 200,
        }
    except Exception as e:
        res = {
            "success": False,
            "errMsg": str(e),  # 将异常转换为字符串
            "code": 0,
        }
    return HttpResponse(json.dumps(res, ensure_ascii=False), content_type="application/json charset=utf-8")


# 图表
D_TRANS = {
    'MAT': '滚动年',
    'QTR': '季度',
    'Value': '金额',
    'Volume': '盒数',
    'Volume (Counting Unit)': '最小制剂单位数',
    '滚动年': 'MAT',
    '季度': 'QTR',
    '金额': 'Value',
    '盒数': 'Volume',
    '最小制剂单位数': 'Volume (Counting Unit)'
}


def prepare_chart(df,  # 输入经过pivoted方法透视过的df，不是原始df
                  chart_type,  # 图表类型字符串，人为设置，根据图表类型不同做不同的Pandas数据处理，及生成不同的Pyechart对象
                  form_dict,  # 前端表单字典，用来获得一些变量作为图表的标签如单位
                  ):
    label = D_TRANS[form_dict['PERIOD_select'][0]] + D_TRANS[form_dict['UNIT_select'][0]]

    if chart_type == 'bar_total_trend':
        df_abs = df.sum(axis=1)  # Pandas列汇总，返回一个N行1列的series，每行是一个date的市场综合
        df_abs.index = df_abs.index.strftime("%Y-%m")  # 行索引日期数据变成2020-06的形式
        df_abs = df_abs.to_frame()  # series转换成df
        df_abs.columns = [label]  # 用一些设置变量为系列命名，准备作为图表标签
        df_gr = df_abs.pct_change(periods=4)  # 获取同比增长率
        df_gr.dropna(how='all', inplace=True)  # 删除没有同比增长率的行，也就是时间序列数据的最前面几行，他们没有同比
        df_gr.replace([np.inf, -np.inf, np.nan], '-', inplace=True)  # 所有分母为0或其他情况导致的inf和nan都转换为'-'
        chart = echarts_stackbar(df=df_abs,
                                 df_gr=df_gr
                                 )  # 调用stackbar方法生成Pyecharts图表对象
        return chart.dump_options()  # 用json格式返回Pyecharts图表对象的全局设置
    elif chart_type == 'bubble_performance':
        df_abs = df.iloc[-1, :]  # 获取最新时间粒度的绝对值
        df_share = df.transform(lambda x: x / x.sum(), axis=1).iloc[-1, :]  # 获取份额
        df_diff = df.diff(periods=4).iloc[-1, :]  # 获取同比净增长

        chart = mpl_bubble(x=df_abs,  # x轴数据
                           y=df_diff,  # y轴数据
                           z=df_share * 50000,  # 气泡大小数据
                           labels=df.columns.str.split('|').str[0],  # 标签数据
                           title='',  # 图表标题
                           x_title=label,  # x轴标题
                           y_title=label + '净增长',  # y轴标题
                           x_fmt='{:,.0f}',  # x轴格式
                           y_fmt='{:,.0f}',  # y轴格式
                           y_avg_line=True,  # 添加y轴分隔线
                           y_avg_value=0,  # y轴分隔线为y=0
                           label_limit=30  # 只显示前30个项目的标签
                           )
        return chart
    elif chart_type == 'predict_chart':
        # 转换日期格式
        df1['DATE'] = pd.to_datetime(df1['DATE'], format='%Y/%m/%d')
        df1['Year'] = df1['DATE'].dt.year  # 提取年份

        # 选择某个 PACKAGE
        selected_package = '安来|AN LAI TAB 75MG 14'
        df_package = df1[df1['PACKAGE'] == selected_package]

        # 按年份计算 AMOUNT 的均值
        df_yearly = df_package.groupby('Year')['AMOUNT'].mean().reset_index()

        # 使用 ARIMA 模型进行预测
        model = ARIMA(df_yearly['AMOUNT'], order=(1, 1, 1))
        model_fit = model.fit()

        # 预测未来3年的 AMOUNT
        future_years = np.array([df_yearly['Year'].max() + i for i in range(1, 4)])
        forecast = model_fit.forecast(steps=3)

        # 将预测结果传递到前端
        prediction_data = list(zip(future_years, forecast))

        # 生成图表
        plt.figure(figsize=(10, 6))
        plt.plot(df_yearly['Year'], df_yearly['AMOUNT'], label='Real Data', color='blue')
        plt.plot(future_years, forecast, label='Forecast', color='green', linestyle='--')

        plt.xlabel('Year')
        plt.ylabel('AMOUNT')
        plt.title(f'{selected_package} AMOUNT prediction (ARIMA)')
        plt.legend()

        # 保存图表到内存
        img = BytesIO()
        plt.savefig(img, format='png')
        img.seek(0)

        # 将图表转换为base64编码，方便在前端展示
        graph_url = base64.b64encode(img.getvalue()).decode('utf-8')

        # 返回渲染模板，并将图表图片和预测数据传递到前端
        return graph_url
    elif chart_type == 'group_chart':
        df1['STRENGTH'] = df1['STRENGTH'].fillna('').astype(str).str.replace('MG', '', regex=False)

        # 确保 STRENGTH 列为数值型
        df1['STRENGTH'] = pd.to_numeric(df1['STRENGTH'], errors='coerce')

        # 2. 数据预处理
        # 选择数值列进行聚类
        num_columns1 = ['AMOUNT']  # 只选择 AMOUNT 列
        num_columns2 = ['AMOUNT', 'STRENGTH']  # 选择 AMOUNT 和 STRENGTH 列

        # 2.1 处理缺失值（删除缺失值的行）
        data_cleaned1 = df1.dropna(subset=num_columns1)  # 只删除 AMOUNT 列有缺失值的行
        data_cleaned2 = df1.dropna(subset=num_columns2)  # 只删除 AMOUNT 和 STRENGTH 列有缺失值的行

        # 2.2 数据标准化
        scaler = StandardScaler()
        data_scaled1 = scaler.fit_transform(data_cleaned1[num_columns1])  # 只对 AMOUNT 列标准化
        data_scaled2 = scaler.fit_transform(data_cleaned2[num_columns2])  # 对 AMOUNT 和 STRENGTH 列标准化

        # 3. 使用肘部法则（Elbow Method）确定最佳的 K 值
        sse1 = []
        for k in range(1, 11):
            kmeans = KMeans(n_clusters=k, random_state=42)
            kmeans.fit(data_scaled1)
            sse1.append(kmeans.inertia_)

        sse2 = []
        for k in range(1, 11):
            kmeans = KMeans(n_clusters=k, random_state=42)
            kmeans.fit(data_scaled2)
            sse2.append(kmeans.inertia_)

        # 4. 选择最佳 K 值（假设肘部法则图显示最佳 K 为 3 对于 AMOUNT，2 对于 AMOUNT 和 STRENGTH）
        kmeans1 = KMeans(n_clusters=2, random_state=42, n_init=10)
        data_cleaned1['Cluster'] = kmeans1.fit_predict(data_scaled1)

        kmeans2 = KMeans(n_clusters=3, random_state=42, n_init=10)
        data_cleaned2['Cluster'] = kmeans2.fit_predict(data_scaled2)

        # 7. 聚类结果的可视化（AMOUNT 列）和（AMOUNT 和 STRENGTH 列）在同一张图上
        plt.figure(figsize=(12, 6))

        # 第一个子图: AMOUNT 列的聚类
        plt.subplot(1, 2, 1)
        plt.scatter(data_cleaned1['AMOUNT'], data_cleaned1['STRENGTH'], c=data_cleaned1['Cluster'], cmap='viridis',
                    s=50)
        plt.xlabel('AMOUNT')
        plt.ylabel('STRENGTH')
        plt.title('Clustering Results for AMOUNT')
        plt.colorbar(label='Cluster')

        # 第二个子图: AMOUNT 和 STRENGTH 列的聚类
        plt.subplot(1, 2, 2)
        plt.scatter(data_cleaned2['AMOUNT'], data_cleaned2['STRENGTH'], c=data_cleaned2['Cluster'], cmap='viridis',
                    s=50)
        plt.xlabel('AMOUNT')
        plt.ylabel('STRENGTH')
        plt.title('Clustering Results for AMOUNT and STRENGTH')
        plt.colorbar(label='Cluster')

        plt.tight_layout()

        # 保存图表到内存
        img = BytesIO()
        plt.savefig(img, format='png')
        img.seek(0)

        # 将图表转换为base64编码，方便在前端展示
        graph_url2 = base64.b64encode(img.getvalue()).decode('utf-8')

        # 返回渲染模板，并将图表图片和预测数据传递到前端
        return graph_url2


@login_required
@cache_page(60 * 60 * 24 * 30)
# 导出为excel
def export(request, type):
    form_dict = dict(six.iterlists(request.GET))

    if type == 'pivoted':
        df = get_df(form_dict)  # 透视后的数据
    elif type == 'raw':
        df = get_df(form_dict, is_pivoted=False)  # 原始数据

    # 创建一个内存中的文件对象
    excel_file = IO()

    # 使用 ExcelWriter 来写入数据
    with pd.ExcelWriter(excel_file, engine='xlsxwriter') as xlwriter:
        # 将 DataFrame 写入 Excel 文件
        df.to_excel(xlwriter, sheet_name='data', index=True)

    # 将文件指针移动到文件的开始位置
    excel_file.seek(0)

    # 设置浏览器 MIME 类型
    response = HttpResponse(excel_file.read(),
                            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    # 设置文件名，带上当前时间戳
    now = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    response['Content-Disposition'] = f'attachment; filename={now}.xlsx'

    return response

@login_required
def index(request):
    mselect_dict = {}
    for key, value in D_MULTI_SELECT.items():
        mselect_dict[key] = {}
        mselect_dict[key]['select'] = value

    context = {
        'mselect_dict': mselect_dict
    }
    return render(request, 'chpa_data/display.html', context)
