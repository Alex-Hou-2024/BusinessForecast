import os, json, time, uuid
from openai import OpenAI
import re
from flask import Flask, request, jsonify, render_template, make_response, Response, send_from_directory
from agentTools import agentTools, registry
import urllib.parse  # ✅ 用于解码 URL 编码的中文文件名
from openai import BadRequestError
from screenshot_utils import capture_webpage_screenshot  # ✅ 加到顶部
import mimetypes

client = OpenAI()
# session_id -> run_id 的映射，用于 cancel 时定位当前会话的运行
run_mapping = {}
thread_mapping = {}


def is_image_file(file_id):
    file_info = client.files.retrieve(file_id)
    filename = file_info.filename
    mime_type, _ = mimetypes.guess_type(filename)
    if mime_type and mime_type.startswith("image/"):
        return True
    else:
        return False

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
                        #print(part.text.value)
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
                                if file_name.endswith('.md') or file_name.endswith('.py') or file_name.endswith('.html'):
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
            # ✅ 记录到 run_mapping
            run_mapping[session_id] = event.data.run_id
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
                    thread_id=thread_mapping[session_id],
                    run_id=run_mapping[session_id],
                    tool_outputs=tool_outputs
            ) as follow_stream:
                # 【核心】递归调用自己，处理 follow_stream
                yield from process_stream(follow_stream, session_id)

def generate(session_id, contents, attachments):
    # 发送消息
    client.beta.threads.messages.create(
        thread_id=thread_mapping[session_id],
        role="user",
        content=contents,
        attachments=attachments if attachments else None
    )

    with client.beta.threads.runs.stream(
            thread_id=thread_mapping[session_id],
            assistant_id=assistant.id,
            instructions="""You are an AI agent specialized in structured data-driven business analysis.""",
    ) as stream:
        yield from process_stream(stream, session_id)

assistant = client.beta.assistants.create(
    name="Structured Execution AI Assistant",
    description="An AI Assistant for structured sales data analysis, trend detection, and interactive dashboard generation.",
    instructions="""You are an AI agent specialized in structured data-driven business analysis.""",
    model="gpt-4.1-mini",
    tools=agentTools,
    response_format={"type": "text"}  # 文本格式输出
)



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

@app.route("/ask", methods=["POST"])
def ask():
    session_id = request.headers.get('Session-ID') or request.cookies.get('session_id') or str(uuid.uuid4())

    # 检查是否有active thread，没有则创建新的thread
    if session_id not in thread_mapping:
        thread_mapping[session_id] = client.beta.threads.create().id

    # 检查是否有active run
    current_run_id = run_mapping.get(session_id)
    if current_run_id:
        # 查询状态
        run = client.beta.threads.runs.retrieve(thread_id=thread_mapping[session_id], run_id=current_run_id)
        if run.status in ["in_progress", "queued", "cancelling"]:
            return jsonify({"error": f"Run {current_run_id} still active: {run.status}"}), 400
        else:
            # ✅ 加这里（增强健壮性和可追溯性）
            print(f"[ASK] Detected stale run: {current_run_id} with status: {run.status}. Auto clearing.")
            run_mapping.pop(session_id, None)

    question = request.form.get('question')
    file_ids = request.form.getlist('file_ids')  # 🔥这里是file_ids

    contents = []

    # 加入文本
    if question:
        contents.append({
            "type": "text",
            "text": question
        })

    attachments = []

    # 判断文件类型
    for file_id in file_ids:
        if is_image_file(file_id):
            # 是图片，加入content
            contents.append({
                "type": "image_file",
                "image_file": {
                    "file_id": file_id
                }
            })
        else:
            # 其他文件，加入带 tools 的 attachments
            attachments.append({
                "file_id": file_id,
                "tools": [{"type": "code_interpreter"}]
            })

    return Response(generate(session_id, contents, attachments), content_type='text/event-stream')

@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory('static', filename)

@app.route("/stop", methods=["POST"])
def stop():
    session_id = request.headers.get('Session-ID') or request.cookies.get('session_id')
    run_id = run_mapping.get(session_id)
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
                    run_mapping.pop(session_id, None)  # ✅ 成功或失败后，都移除
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
