import {fireEvent,render,screen,waitFor} from '@testing-library/react'
import {describe,it,expect,vi,beforeEach} from 'vitest'
import StudioPresentationRepair from './StudioPresentationRepair'
import * as api from '../lib/api'
vi.mock('../lib/api',()=>({fetchPresentationRepair:vi.fn(),applyPresentationRepair:vi.fn()}))
beforeEach(()=>vi.clearAllMocks())
describe('Studio registered presentation repair',()=>{
  it('retains one immutable request after an uncertain response and does not double submit',async()=>{
    vi.mocked(api.fetchPresentationRepair).mockResolvedValue({can_repair:true,reason:'',scope:'renderer_and_downstream_only',package_sha256:'a'.repeat(64),renderer_source:'b'.repeat(64)})
    vi.mocked(api.applyPresentationRepair).mockRejectedValueOnce(new Error('response lost')).mockResolvedValueOnce({id:'same-order'} as never)
    const changed=vi.fn().mockResolvedValue(undefined)
    render(<StudioPresentationRepair jobId="same-order" onChanged={changed}/>);
    fireEvent.click(screen.getByText('Sprawdź dostępność poprawki'))
    fireEvent.click(await screen.findByText('Zleć poprawkę montażu'))
    expect(await screen.findByRole('alert')).toHaveTextContent('response lost')
    const first=vi.mocked(api.applyPresentationRepair).mock.calls[0]
    expect(screen.getByLabelText('Powód poprawki montażu')).toBeDisabled()
    const retry=screen.getByText('Sprawdź lub ponów tę samą poprawkę')
    fireEvent.click(retry);fireEvent.click(retry)
    await waitFor(()=>expect(changed).toHaveBeenCalledTimes(1))
    expect(api.applyPresentationRepair).toHaveBeenCalledTimes(2)
    expect(vi.mocked(api.applyPresentationRepair).mock.calls[1]).toEqual(first)
  })
  it('shows the actual blocker instead of allowing an unsafe correction',async()=>{
    vi.mocked(api.fetchPresentationRepair).mockResolvedValue({can_repair:false,reason:'Current review is missing',scope:'renderer_and_downstream_only'})
    render(<StudioPresentationRepair jobId="blocked" onChanged={vi.fn()}/>);
    fireEvent.click(screen.getByText('Sprawdź dostępność poprawki'))
    expect(await screen.findByRole('status')).toHaveTextContent('Current review is missing')
    expect(screen.queryByText('Zleć poprawkę montażu')).not.toBeInTheDocument()
    expect(api.applyPresentationRepair).not.toHaveBeenCalled()
  })
  it('reconciles a lost response before preparing another action',async()=>{
    vi.mocked(api.fetchPresentationRepair)
      .mockResolvedValueOnce({can_repair:true,reason:'',scope:'renderer_and_downstream_only',package_sha256:'a'.repeat(64),renderer_source:'b'.repeat(64)})
      .mockResolvedValueOnce({can_repair:false,reason:'Correction is already running',scope:'renderer_and_downstream_only'})
    vi.mocked(api.applyPresentationRepair).mockRejectedValue(new Error('response lost'))
    const changed=vi.fn().mockResolvedValue(undefined)
    render(<StudioPresentationRepair jobId="same-order" onChanged={changed}/>);
    fireEvent.click(screen.getByText('Sprawdź dostępność poprawki'))
    fireEvent.click(await screen.findByText('Zleć poprawkę montażu'))
    fireEvent.click(await screen.findByText('Sprawdź aktualny pakiet i dostępność'))
    expect(await screen.findByRole('status')).toHaveTextContent('Correction is already running')
    expect(changed).toHaveBeenCalledTimes(1)
    expect(api.applyPresentationRepair).toHaveBeenCalledTimes(1)
  })
})
