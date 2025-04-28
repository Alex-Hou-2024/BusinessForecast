import os, json, time, uuid
from openai import OpenAI

from flask import Flask, request, jsonify, render_template, make_response, Response, send_from_directory
from agentTools import agentTools


client = OpenAI()

# Step 1: 创建 Assistant（只需要创建一次）
assistant = client.beta.assistants.create(
    name="Data analyst",
    description="You are an economic data analyst, analyzing various uploaded files and then answering the questions submitted by users",
    instructions="You are an economic data analyst, analyzing various uploaded files and then answering the questions submitted by users",
    model="gpt-4.1-mini",
    tools=agentTools,
    response_format={"type": "text"}  # 文本格式输出
)

# Step 2: 创建会话（Thread）
thread = client.beta.threads.create()

# session_id -> run_id 的映射，用于 cancel 时定位当前会话的运行
session_run_mapping = {}
self_run_id = ""  # 需要补上run_id，如果能在最开始拿到

app = Flask(__name__, static_folder='static', template_folder='templates')

@app.route("/")
@app.route("/AI")
def index_page():
    session_id = request.cookies.get('session_id')
    if not session_id:
        session_id = str(uuid.uuid4())
        resp = make_response(render_template('index3.html'))
        resp.set_cookie('session_id', session_id)
        return resp
    return render_template('index3.html')

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


@app.route("/ask", methods=["POST"])
def ask():
    session_id = request.headers.get('Session-ID') or request.cookies.get('session_id') or str(uuid.uuid4())
    question = request.form.get('question')
    file_ids = request.form.getlist('file_ids')  # 🔥这里是file_ids

    attachments = []
    for file_id in file_ids:
        attachments.append({
            "file_id": file_id,
            "tools": [{"type": "code_interpreter"}]
        })

    # 然后继续你的 assistant 处理
    ...

    # Step 4: 向 Assistant 发消息，并附加上传的文件
    client.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content=question,
        attachments=attachments
    )

    def process_stream(stream):
        global self_run_id
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

            elif event.event == "thread.run.step.created":
                if event.data.step_details.type == "tool_calls":
                    self_run_id = event.data.run_id
                    #yield f"\n\nassistant tool call: {event.data.step_details.type} >>\n\n"
                    #yield f"\n\n```python\n\n"
                    #print({"type": "code_start"})
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
                                    if output.type == "logs":
                                        #yield output.logs
                                        #print(output.logs)
                                        yield json.dumps({"type": "code_content", "content": output.logs}) + "\n"

            elif event.event == "thread.run.step.completed":
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
                    #if tool_call.function.name == "example_blog_post_function_1":
                        #result = my_example_funtion()
                        #tool_outputs.append({
                        #    "tool_call_id": tool_call.id,
                        #    "output": "上升"
                        #})
                    #elif tool_call.function.name == "example_blog_post_function_2":
                        #result = my_example_funtion()
                        #tool_outputs.append({
                        #    "tool_call_id": tool_call.id,
                        #    "output": "下降"
                        #})

                # 提交输出后，继续打开新的 follow_stream
                with client.beta.threads.runs.submit_tool_outputs_stream(
                        thread_id=thread.id,
                        run_id=self_run_id,
                        tool_outputs=tool_outputs
                ) as follow_stream:
                    # 【核心】递归调用自己，处理 follow_stream
                    yield from process_stream(follow_stream)

    def generate():
        with client.beta.threads.runs.stream(
                thread_id=thread.id,
                assistant_id=assistant.id,
                instructions="Your name is Sasha and the client's name is Big Smart"
        ) as stream:
            yield from process_stream(stream)

    return Response(generate(), content_type='text/event-stream')

@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory('static', filename)


from openai import BadRequestError

@app.route("/stop", methods=["POST"])
def stop():
    session_id = request.headers.get('Session-ID') or request.cookies.get('session_id')
    run_id = session_run_mapping.get(session_id)

    if self_run_id:
        try:
            run = client.beta.threads.runs.cancel(thread_id=thread.id, run_id=self_run_id)
            print(f"Cancel request sent. Initial status: {run.status}")

            # 轮询确认取消完成
            for _ in range(10):
                time.sleep(0.5)
                run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=self_run_id)
                if run.status == "cancelled":
                    print("Run successfully cancelled.")
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


if __name__ == "__main__":
    app.run(debug=False, host='0.0.0.0', port=8000)
