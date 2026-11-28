import {it,expect} from 'vitest'
import {downloadFilename} from './download-filename'
it('reads unicode and ordinary attachment names with safe fallbacks',()=>{
 expect(downloadFilename('attachment; filename="monthly-report-1.pdf"')).toBe('monthly-report-1.pdf')
 expect(downloadFilename("attachment; filename=backup.pdf; filename*=UTF-8''2026-07_%EC%82%AC%EA%B3%BC.pdf")).toBe('2026-07_사과.pdf')
 expect(downloadFilename("attachment; filename=backup.pdf; filename*=UTF-8''%ZZ")).toBe('backup.pdf')
 expect(downloadFilename('attachment; filename="../report.pdf"')).toBe('report.pdf')
})
