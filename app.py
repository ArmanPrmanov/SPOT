from flask import Flask, jsonify, request
import time
import mmap
import os

app = Flask(__name__)


@app.route("/")
def default():
    return "Hello world.\n"


@app.route("/hello/<id>")
def hello(id=0):
    try:
        id = int(id)
        id += 2
    except ValueError:
        pass
    return f"{id}\n"


# UNUSED: the task-simulation endpoints below (process_task, exec_task,
# upload_to_mec) are never called. Containers run app.py only as a
# long-running foreground process (keepalive); transfers are measured by
# xfer_send.py / iperf and compute time is modelled in network_graph.py.
# upload_to_mec writes a fixed-name temp_file.mmap, so it is not safe under
# concurrent requests.
@app.route('/process/<task_id>/<memory>/<cpu>', methods=['GET'])
def process_task(task_id = 0, memory = 1, cpu = 1):
    return f'task id {task_id}, memory is {memory}MB, CPU is {cpu}\n'

@app.route('/process/<task_id>/<cpu>', methods=['GET'])
def exec_task(task_id = 0, memory = 1, cpu = 1):
    time.sleep(float(cpu)//1000)
    return f"sleep: {float(cpu)//1000}"

@app.route('/upload_2_mec/<task_id>/<memory>/<cpu>/<successors>')
def upload_to_mec(task_id = '', memory=10, cpu = 0, successors=None):
    successors = successors.split(",")
    res = []

    for successor in successors:
        res.append(task_id)
    # Create a memory-mapped file (it doesn't physically write data to disk)
    filename = 'temp_file.mmap'

    # We create an empty file and open it in read/write mode
    with open(filename, 'wb') as f:
        f.write(b'\0' * (int(memory) * 1024 * 1024))  # Allocate 'memory' MB, but no actual data is written

    # Memory map the file and load it into memory
    with open(filename, "r+b") as f:
        mmapped_file = mmap.mmap(f.fileno(), 0)  # Memory map the entire file
        # Access some part of the file to ensure it's loaded
        mmapped_file[0:10]


    time.sleep(float(cpu)//1000)
    os.remove(filename)

    return res

# @app.route('/upload_2_mec_json', methods=['POST'])
# def upload_to_mec():
#     data = request.get_json()
    # task_id = data.get('task_id')
    # memory = data.get('memory')
    # cpu = data.get('cpu')
    # successors = data.get('successors', [])
    # res = []
    #
    # for successor in successors:
    #     res.append(task_id)
    # # Create a memory-mapped file (it doesn't physically write data to disk)
    # filename = 'temp_file.mmap'
    #
    # # We create an empty file and open it in read/write mode
    # with open(filename, 'wb') as f:
    #     f.write(b'\0' * (int(memory) * 1024 * 1024))  # Allocate 'memory' MB, but no actual data is written
    #
    # # Memory map the file and load it into memory
    # with open(filename, "r+b") as f:
    #     mmapped_file = mmap.mmap(f.fileno(), 0)  # Memory map the entire file
    #     # Access some part of the file to ensure it's loaded
    #     mmapped_file[0:10]
    #
    # time.sleep(float(cpu) // 100)
    # os.remove(filename)

    # return res

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=80)
