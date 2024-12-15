
var file_select = $('#current-file-select')
var btn_load = $('#btn-load')
var btn_delete = $('#btn-delete')
var form_cmd = $('#form-cmd')
var text_cmd = $('#text-cmd')
var cmd_to_send = $('#cmd-to-send')
var cmd_log = $('#cmd-log')
var cmd_queue = $('#cmd-queue')

$('#btn-send').click(function() {
    var commands = cmd_to_send.val()
    $.post('/api/exec', { cmd: commands }).done(function(data) {
        if(data.status != 'ok') {
            alert(data.status)
        }
    })
    return false
})

$('button[value]').click(function(obj) {
    var command = $(this).val()
    var text = $(this).text()
    if(command == 'hold' || command == 'resume' || confirm('Execute ' + text + ' action?')) {
        $.post('/api/command', { cmd: command }).done(function(data) {
            if(data.status != 'ok') {
                alert(data.status)
            }
        })
    }
    return false
})

btn_load.click(function() {
    var file = file_select.val()
    $.post('/api/load', { file: file }).done(function(data) {
        if(data.status != 'ok') {
            alert(data.status)
        } else {
            if(data.gcode) {
                cmd_to_send.val(data.gcode.join('\n'))
            }
        }
    })
    return false
})

btn_delete.click(function() {
    var file = file_select.val()
    if(confirm('Delete ' + file + '?')) {
        $.post('/api/delete', { file: file }).done(function(data) {
            if(data.status != 'ok') {
                alert(data.status)
            } else {
                $('[value="'+ file + '"]', file_select).remove()
            }
        })
    }
    return false
})

form_cmd.submit(function() {
    command = text_cmd.val().trim()
    text_cmd.val('')
    if(command) {
        $.post('/api/exec', { cmd: command }).done(function(data) {
            if(data.status != 'ok') {
                alert(data.status)
            }
        })
    }
    return false
})

var last_log_id = -1
var last_log_time = 0
setInterval(function() {
    $.ajax('/api/info', {
        data: {time: Math.floor(last_log_time)},
        timeout: 2000
    }).done(function(data) {
        if(data.status == 'ok') {
            var log_area = cmd_log.get(0)
            var follow = log_area.scrollTop > log_area.scrollHeight - log_area.clientHeight - 32
            for(l of data.log) {
                if(l.id < last_log_id && l.time > (last_log_time + 0.01)) {
                    last_log_id = -1
                }
                if(l.id > last_log_id) {
                    cmd_log.val(function(index, old){ return old + l.msg + '\n' })
                    last_log_id = l.id
                    last_log_time = l.time
                    if(follow) {
                        log_area.scrollTop = log_area.scrollHeight
                    }
                }
            }
            cmd_queue.val(data.queue.join('\n'))
            $('#text-grbl-state').val(data.state)
            $('#title-file-name').text(data.file ? data.file : '<Root dir>')
        } else {
            $('#text-grbl-state').val(data.status)
        }
    }).fail(function() {
        $('#text-grbl-state').val('No connection')
    })
}, 2000)
