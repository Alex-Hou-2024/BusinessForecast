import os, json, time, uuid
from openai import OpenAI
import re
from flask import Flask, request, jsonify, render_template, make_response, Response, send_from_directory
from agentTools import agentTools, registry
import urllib.parse  # ✅ 用于解码 URL 编码的中文文件名
from openai import BadRequestError
from screenshot_utils import capture_webpage_screenshot  # ✅ 加到顶部

client = OpenAI()

# Step 1: 创建 Assistant（只需要创建一次）
assistant = client.beta.assistants.create(
    name="AI Sales Data Analyst & Dashboard Creator",
    description="""
Automatically analyzes uploaded sales logs, generates markdown reports, performs product trend analysis using Google Trends & Search, and creates interactive, fully self-contained HTML dashboards.
All reports and dashboards are generated as downloadable files.
                """,
    instructions="""
The uploaded file is a sales log (e.g., Excel, CSV).  
Please perform the following structured steps carefully in sequence. This is a Chain of Thought (CoT) task.  
For each step, start your output with **Step X: [Task Title]**, where X is the step number (1, 2, 3, 4).

---

Step 1: Sales Data Analysis (Overall and by Category)
- Parse the uploaded sales log and analyze sales data.
- For all detected **Category Names**, generate a **Markdown report** showing key metrics per category (e.g., GMV, Sales Volume, Top Products).
- Build a **fully self-contained, single-file HTML dashboard**.
   - The content, filters, charts, tables, and interaction features of the dashboard must be **automatically decided by the Assistant** based on the actual uploaded data and its structure.
   - The Assistant must decide the best set of dropdowns, sliders, charts, and visualizations based on the data characteristics (such as category, product, region, sales trend, etc.).
   - The dashboard must support dynamic filtering, hover tooltips, and be fully responsive and mobile-friendly.
- ✅ Ensure both the Markdown report and HTML dashboard are generated as files and provided with direct download links for the user.

---

Step 2: Category Breakdown
- List all detected categories.
- For each category, breakdown and list the **Top 10 products** based on sales.
- Output as a **Markdown report**.
- ✅ Ensure the report is saved as a Markdown file and provided for download.

---

Step 3: Product Trend Analysis (Google Trends)
- From the previously listed categories, pick any **5 categories**.
- For each of these 5 categories, select **10 products** (same top 10 from step 2).
- Use **Google Trends tool-calling** to search each product.
- Summarize the search volume trends.
- ✅ All trend data summaries must be output as a Markdown file and provided for download.

---

Step 4: Detailed Spotlight on Top Trending Product
- Identify the **single product with the highest Google Trends search volume** among those analyzed in step 3.
- Generate a **line chart (trend over time) using Google Trends data**.
- Use **Google Search tool-calling** to find the product's official homepage or best source page.
- Summarize key findings.
- ✅ Ensure the trend chart is embedded in a Markdown file and the result is also saved as a downloadable Markdown report.

---

**Strict Output Requirements:**
- All outputs including reports, dashboards, charts **must be saved as files (Markdown and HTML) and provided as downloadable links for the user.**
- All dashboards must be fully self-contained HTML files (embedding all CSS, JS, data), ready for local use without server dependency.
- All reports must be clean, well-structured Markdown files.
- Use embedded JS libraries (e.g., Chart.js, ApexCharts, or Plotly via CDN) inside the HTML dashboard.
- The dashboard and all reports must be cleanly named, user-friendly, and downloadable directly.

**Important:**  
Use **Google Trends and Google Search tools responsibly to ensure data accuracy**.  
All steps must be clearly separated and titled in your output, starting with **Step X: [Title]**.  
Do not skip any steps.  
Ensure the outputs are actionable, professionally formatted, and support strategic decision-making.
                 """,

    model="gpt-4.1-mini",
    tools=agentTools,
    response_format={"type": "text"}  # 文本格式输出
)

# Step 2: 创建会话（Thread）
thread = client.beta.threads.create()

# session_id -> run_id 的映射，用于 cancel 时定位当前会话的运行
session_run_mapping = {}

app = Flask(__name__, static_folder='static', template_folder='templates')

@app.route("/")
@app.route("/AI")
def index_page():
    session_id = request.cookies.get('session_id')
    if not session_id:
        session_id = str(uuid.uuid4())
        resp = make_response(render_template('index6.html'))
        resp.set_cookie('session_id', session_id)
        return resp
    return render_template('index6.html')

thread_mapping = {}
@app.route("/ask", methods=["POST"])
def ask():
    session_id = request.headers.get('Session-ID') or request.cookies.get('session_id') or str(uuid.uuid4())

    if session_id not in thread_mapping:
        thread_mapping[session_id] = client.beta.threads.create().id

    thread_id = thread_mapping[session_id]

    # 检查是否有active run
    current_run_id = session_run_mapping.get(session_id)
    if current_run_id:
        # 查询状态
        run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=current_run_id)
        if run.status in ["in_progress", "queued", "cancelling"]:
            return jsonify({"error": f"Run {current_run_id} still active: {run.status}"}), 400
        else:
            # ✅ 加这里（增强健壮性和可追溯性）
            print(f"[ASK] Detected stale run: {current_run_id} with status: {run.status}. Auto clearing.")
            session_run_mapping.pop(session_id, None)

    question = request.form.get('question')
    file_ids = request.form.getlist('file_ids')  # 🔥这里是file_ids

    attachments = []
    for file_id in file_ids:
        attachments.append({
            "file_id": file_id,
            "tools": [{"type": "code_interpreter"}]
        })

    # Step 4: 向 Assistant 发消息，并附加上传的文件
    client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=question,
        attachments=attachments
    )

    def process_stream(stream, session_id):
        for event in stream:
            if event.event == "thread.message.created":
                pass
                #yield f"\n\n{event.data.role} >>\n\n"

            elif event.event == "thread.message.delta":
                delta = event.data.delta
                if delta.content:
                    for part in delta.content:
                        if part.type == "text":
                            print(part.text.value)
                            yield json.dumps({"type": "text", "content": part.text.value}) + "\n"
                        elif part.type == "image_file":
                            print(part.image_file)
                            image_file = client.files.content(part.image_file.file_id)
                            file_name=part.image_file.file_id

                            with open(f"download/{file_name}.png", "wb") as f:
                                f.write(image_file.content)

                            yield json.dumps({
                                "type": "text",
                                "content": f"The picture has been generated.：\n\n<img src='/download/{file_name}.png' style='width:400px;'>\n\n"
                            })

            elif event.event == "thread.message.completed":
                content = event.data.content
                if content:
                    for part in content:
                        if part.type == "text":
                            print(part.text.value)
                            for annotation in part.text.annotations:
                                if annotation.type == "file_path":
                                    file_name = os.path.basename(annotation.text)
                                    file_content = client.files.content(annotation.file_path.file_id)

                                    with open(f"download/{file_name}", "wb") as f:
                                        f.write(file_content.read())  # ✅ 读取流中的字节内容

                                    # 判断是否是 Markdown 文件，生成带 onclick 的 HTML 链接
                                    if file_name.endswith('.md') or file_name.endswith('.markdown') or file_name.endswith('.html'):
                                        preview_link = f"<a href='#' onclick=\"previewMarkdown('{file_name}')\">preview {file_name}</a>"
                                        yield json.dumps({
                                            "type": "preview",
                                            "file_name": file_name
                                        })
                                    else:
                                        preview_link = f"[onclick {file_name}](/download/{urllib.parse.quote(file_name)})"

                                    yield json.dumps({
                                        "type": "text",
                                        "content": f"\n\n{preview_link}\n\n"
                                    })


            elif event.event == "thread.run.step.created":
                # ✅ 记录到 session_run_mapping
                session_run_mapping[session_id] = event.data.run_id
                if event.data.step_details.type == "tool_calls":
                    yield json.dumps({"type": "code_start"}) + "\n"

            elif event.event == "thread.run.step.delta":
                delta = event.data.delta
                if delta.step_details.type == "tool_calls":
                    tool_calls = delta.step_details.tool_calls
                    for tool_call in tool_calls:
                        if tool_call.type == "code_interpreter":
                            code_input = tool_call.code_interpreter.input
                            if code_input:
                                #yield code_input
                                #print(code_input)
                                yield json.dumps({"type": "code_content", "content": code_input}) + "\n"

                            outputs = tool_call.code_interpreter.outputs
                            if outputs:
                                for output in outputs:
                                    print("tool_call.code_interpreter.output.type: " + output.type)
                                    if output.type == "logs":
                                        #yield output.logs
                                        #print(output.logs)
                                        yield json.dumps({"type": "code_content", "content": "\n\n" + output.logs}) + "\n"

            elif event.event == "thread.run.step.completed":
                print("event.data.step_details.type:" + event.data.step_details.type)
                if event.data.step_details.type == "tool_calls":
                    #yield f"\n\n```\n\n"
                    #print({"type": "code_end"})
                    yield json.dumps({"type": "code_end"}) + "\n"

            elif event.event == "thread.run.requires_action":
                # 这里遇到 requires_action，需要调用工具，并递归继续处理
                tool_calls = event.data.required_action.submit_tool_outputs.tool_calls
                tool_outputs = []

                for tool_call in tool_calls:
                    arguments = json.loads(tool_call.function.arguments)

                    if tool_call.function.name not in registry:
                        print('No suitable action found. Please confirm that your input is valid. \n')
                        raise ValueError(f"函数 {tool_call.function.name} 不存在")
                    else:
                        result = registry[tool_call.function.name](arguments)

                        # 处理 Product Image 为 markdown 图片
                        image_match = re.search(r"Product Image:\s*(https?://[^\s]+)", result)
                        if image_match:
                            image_url = image_match.group(1)
                            result += f"\n\n![product image]({image_url})\n"

                        # 发送 event.type=web_preview 用于右侧 iframe
                        link_match = re.search(r"Link:\s*(https?://[^\s]+)", result)
                        if link_match:
                            preview_url = link_match.group(1).rstrip(",. \n")  # ✅ 自动清洗末尾符号
                            # ✅ 发给前端右侧预览 iframe
                            #yield json.dumps({
                            #    "type": "web_preview",
                            #    "url": preview_url
                            #})

                            # ✅ 自动截图，保存在 static/screenshots 中
                            try:
                                file_id = str(uuid.uuid4())
                                screenshot_path = f"static/screenshots/{file_id}.jpeg"
                                from screenshot_utils import capture_webpage_screenshot
                                capture_webpage_screenshot(preview_url, save_path=screenshot_path, width=1200,
                                                           height=800)

                                yield json.dumps({
                                    "type": "preview",
                                    "file_name": f"screenshots/{file_id}.jpeg"
                                })
                            except Exception as e:
                                print(f"截图失败: {e}")

                        tool_outputs.append({
                            "tool_call_id": tool_call.id,
                            "output": result
                        })

                # 提交输出后，继续打开新的 follow_stream
                with client.beta.threads.runs.submit_tool_outputs_stream(
                        thread_id=thread.id,
                        run_id=session_run_mapping.get(session_id),
                        tool_outputs=tool_outputs
                ) as follow_stream:
                    # 【核心】递归调用自己，处理 follow_stream
                    yield from process_stream(follow_stream)

    def generate(session_id):
        thread_id = thread_mapping[session_id]
        with client.beta.threads.runs.stream(
                thread_id=thread_id,
                assistant_id=assistant.id,
                instructions="""
The uploaded file is a sales log.
Please follow the steps below strictly and sequentially:
Analyze sales data, generate category-wise markdown report, and build a fully self-contained HTML dashboard (with all filters, charts, tables, download option).
Break down all detected categories into their top 10 products; output as markdown report.
Select 5 categories, analyze top 10 products in each using Google Trends; output as markdown.
Pick the top trending product, generate its detailed report, including trend chart and homepage search result; output as markdown report.
All outputs must be files (Markdown and HTML) and provide download links. The dashboard must be a single-file HTML, fully embedded, and usable locally.
Use Google Trends and Google Search tool-calling carefully and ensure data accuracy.
All steps are mandatory. Ensure clarity, professionalism, and actionability in all reports and dashboards.
                            """
        ) as stream:
            yield from process_stream(stream, session_id)

    return Response(generate(session_id), content_type='text/event-stream')

@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory('static', filename)

@app.route("/stop", methods=["POST"])
def stop():
    session_id = request.headers.get('Session-ID') or request.cookies.get('session_id')
    run_id = session_run_mapping.get(session_id)
    thread_id = thread_mapping.get(session_id)

    if run_id and thread_id:
        try:
            run = client.beta.threads.runs.cancel(thread_id=thread_id, run_id=run_id)
            print(f"Cancel request sent. Initial status: {run.status}")

            # 轮询确认取消完成
            for _ in range(10):
                time.sleep(0.5)
                run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run_id)
                if run.status == "cancelled":
                    print("Run successfully cancelled.")
                    session_run_mapping.pop(session_id, None)  # ✅ 成功或失败后，都移除
                    return {'status': 'cancelled'}
                elif run.status not in ("cancelling", "in_progress"):
                    print(f"Run status became: {run.status}")
                    return {'status': run.status}

            # 超时
            return {'status': run.status}

        except BadRequestError as e:
            error_message = str(e)
            if "Cannot cancel run with status 'cancelled'" in error_message:
                print("Run already cancelled.")
                return {'status': 'cancelled'}
            else:
                print(f"Stop error: {error_message}")
                return {'status': 'error', 'message': error_message}, 500
    else:
        return {'status': 'no active run'}

@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    temp_path = f"tmp/{file.filename}"  # 注意路径
    os.makedirs(os.path.dirname(temp_path), exist_ok=True)  # 确保tmp文件夹存在
    file.save(temp_path)

    # 🔥 用 with 打开，确保上传后自动关闭文件
    with open(temp_path, "rb") as f:
        uploaded = client.files.create(file=f, purpose="assistants")

    os.remove(temp_path)  # 🔥 文件已经关闭，安全删除

    return jsonify({"file_id": uploaded.id})

@app.route("/download/<path:filename>")
def download_file(filename):
    safe_filename = urllib.parse.unquote(filename)

    # 如果是截图
    if safe_filename.startswith("screenshots/"):
        file_path = os.path.join("static", safe_filename)
        if not os.path.isfile(file_path):
            return "File not found", 404
        # 直接从 static 返回
        return send_from_directory("static", safe_filename, as_attachment=False)

    # 否则正常下载
    file_path = os.path.join("download", safe_filename)
    if not os.path.isfile(file_path):
        return "File not found", 404

    if safe_filename.lower().endswith('.html'):
        with open(file_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        return Response(html_content, mimetype='text/html')

    return send_from_directory("download", safe_filename, as_attachment=False)

if __name__ == "__main__":
    app.run(debug=False, host='0.0.0.0', port=8000)
